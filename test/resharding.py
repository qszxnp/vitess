#!/usr/bin/env python
#
# Copyright 2013, Google Inc. All rights reserved.
# Use of this source code is governed by a BSD-style license that can
# be found in the LICENSE file.

"""This test covers a resharding scenario of an already sharded keyspace.

We start with shards -80 and 80-. We then split 80- into 80-c0 and c0-.

This test is the main resharding test. It not only tests the regular resharding
workflow for an horizontal split, but also a lot of error cases and side
effects, like:
- migrating the traffic one cell at a time.
- migrating rdonly traffic back and forth.
- making sure we can't migrate the master until replica and rdonly are migrated.
- has a background thread to insert data during migration.
- tests a destination shard master failover while replication is running.
- tests a filtered replication source replacement while filtered replication
  is running.
- tests 'vtctl SourceShardAdd' and 'vtctl SourceShardDelete'.
- makes sure the key range rules are properly enforced on masters.
"""

import struct
import threading
import time

import logging
import unittest

from vtproto import topodata_pb2

from vtdb import keyrange_constants

import base_sharding
import environment
import tablet
import utils

keyspace_id_type = keyrange_constants.KIT_UINT64
pack_keyspace_id = struct.Struct('!Q').pack

# initial shards
# range '' - 80
shard_0_master = tablet.Tablet()
shard_0_replica = tablet.Tablet()
shard_0_ny_rdonly = tablet.Tablet(cell='ny')
# range 80 - ''
shard_1_master = tablet.Tablet()
shard_1_slave1 = tablet.Tablet()
shard_1_slave2 = tablet.Tablet()
shard_1_ny_rdonly = tablet.Tablet(cell='ny')
shard_1_rdonly1 = tablet.Tablet()

# split shards
# range 80 - c0
shard_2_master = tablet.Tablet()
shard_2_replica1 = tablet.Tablet()
shard_2_replica2 = tablet.Tablet()
# range c0 - ''
shard_3_master = tablet.Tablet()
shard_3_replica = tablet.Tablet()
shard_3_rdonly1 = tablet.Tablet()

all_tablets = [shard_0_master, shard_0_replica, shard_0_ny_rdonly,
               shard_1_master, shard_1_slave1, shard_1_slave2,
               shard_1_ny_rdonly, shard_1_rdonly1,
               shard_2_master, shard_2_replica1, shard_2_replica2,
               shard_3_master, shard_3_replica, shard_3_rdonly1]


def setUpModule():
  try:
    environment.topo_server().setup()
    setup_procs = [t.init_mysql() for t in all_tablets]
    utils.Vtctld().start()
    utils.wait_procs(setup_procs)
  except:
    tearDownModule()
    raise


def tearDownModule():
  utils.required_teardown()
  if utils.options.skip_teardown:
    return

  teardown_procs = [t.teardown_mysql() for t in all_tablets]
  utils.wait_procs(teardown_procs, raise_on_error=False)
  environment.topo_server().teardown()
  utils.kill_sub_processes()
  utils.remove_tmp_files()
  for t in all_tablets:
    t.remove_tree()


# InsertThread will insert a value into the timestamps table, and then
# every 1/5s will update its value with the current timestamp
class InsertThread(threading.Thread):

  def __init__(self, tablet_obj, object_name, user_id, custom_sharding_key):
    threading.Thread.__init__(self)
    self.tablet = tablet_obj
    self.object_name = object_name
    self.user_id = user_id
    self.custom_sharding_key = custom_sharding_key
    self.str_custom_sharding_key = utils.uint64_to_hex(custom_sharding_key)
    self.done = False

    self.tablet.mquery(
        'vt_test_keyspace',
        ['begin',
         'insert into timestamps(name, time_milli, custom_sharding_key) '
         "values('%s', %d, 0x%x) "
         '/* vtgate:: keyspace_id:%s */ /* user_id:%d */' %
         (self.object_name, long(time.time() * 1000), self.custom_sharding_key,
          self.str_custom_sharding_key, self.user_id),
         'commit'],
        write=True, user='vt_app')
    self.start()

  def run(self):
    try:
      while not self.done:
        self.tablet.mquery(
            'vt_test_keyspace',
            ['begin',
             'update timestamps set time_milli=%d '
             'where name="%s" /* vtgate:: keyspace_id:%s */ /* user_id:%d */' %
             (long(time.time() * 1000), self.object_name,
              self.str_custom_sharding_key, self.user_id),
             'commit'],
            write=True, user='vt_app')
        time.sleep(0.2)
    except Exception:
      logging.exception('InsertThread got exception.')


# MonitorLagThread will get values from a database, and compare the timestamp
# to evaluate lag. Since the qps is really low, and we send binlogs as chuncks,
# the latency is pretty high (a few seconds).
class MonitorLagThread(threading.Thread):

  def __init__(self, tablet_obj, object_name):
    threading.Thread.__init__(self)
    self.tablet = tablet_obj
    self.object_name = object_name
    self.done = False
    self.max_lag = 0
    self.lag_sum = 0
    self.sample_count = 0
    self.start()

  def run(self):
    try:
      while not self.done:
        result = self.tablet.mquery(
            'vt_test_keyspace',
            'select time_milli from timestamps where name="%s"' %
            self.object_name)
        if result:
          lag = long(time.time() * 1000) - long(result[0][0])
          logging.debug('MonitorLagThread(%s) got %d', self.object_name, lag)
          self.sample_count += 1
          self.lag_sum += lag
          if lag > self.max_lag:
            self.max_lag = lag
        time.sleep(1.0)
    except Exception:
      logging.exception('MonitorLagThread got exception.')


class TestResharding(unittest.TestCase, base_sharding.BaseShardingTest):

  # create_schema will create the same schema on the keyspace
  # then insert some values
  def _create_schema(self):
    if keyspace_id_type == keyrange_constants.KIT_BYTES:
      t = 'varbinary(64)'
    else:
      t = 'bigint(20) unsigned'
    create_table_template = '''create table %s(
id bigint not null,
msg varchar(64),
custom_sharding_key ''' + t + ''' not null,
primary key (id),
index by_msg (msg)
) Engine=InnoDB'''
    create_view_template = (
        'create view %s'
        '(id, msg, custom_sharding_key) as select id, msg, custom_sharding_key '
        'from %s')
    create_timestamp_table = '''create table timestamps(
name varchar(64),
time_milli bigint(20) unsigned not null,
custom_sharding_key ''' + t + ''' not null,
primary key (name)
) Engine=InnoDB'''
    create_unrelated_table = '''create table unrelated(
name varchar(64),
primary key (name)
) Engine=InnoDB'''

    utils.run_vtctl(['ApplySchema',
                     '-sql=' + create_table_template % ('resharding1'),
                     'test_keyspace'],
                    auto_log=True)
    utils.run_vtctl(['ApplySchema',
                     '-sql=' + create_table_template % ('resharding2'),
                     'test_keyspace'],
                    auto_log=True)
    utils.run_vtctl(['ApplySchema',
                     '-sql=' + create_view_template % ('view1', 'resharding1'),
                     'test_keyspace'],
                    auto_log=True)
    utils.run_vtctl(['ApplySchema',
                     '-sql=' + create_timestamp_table,
                     'test_keyspace'],
                    auto_log=True)
    utils.run_vtctl(['ApplySchema',
                     '-sql=' + create_unrelated_table,
                     'test_keyspace'],
                    auto_log=True)

  # _insert_value inserts a value in the MySQL database along with the comments
  # required for routing.
  def _insert_value(self, tablet_obj, table, mid, msg, custom_sharding_key):
    k = utils.uint64_to_hex(custom_sharding_key)
    tablet_obj.mquery(
        'vt_test_keyspace',
        ['begin',
         'insert into %s(id, msg, custom_sharding_key) '
         'values(%d, "%s", 0x%x) /* vtgate:: keyspace_id:%s */ '
         '/* user_id:%d */' %
         (table, mid, msg, custom_sharding_key, k, mid),
         'commit'],
        write=True)

  def _get_value(self, tablet_obj, table, mid):
    return tablet_obj.mquery(
        'vt_test_keyspace',
        'select id, msg, custom_sharding_key from %s where id=%d' %
        (table, mid))

  def _check_value(self, tablet_obj, table, mid, msg, custom_sharding_key,
                   should_be_here=True):
    result = self._get_value(tablet_obj, table, mid)
    if keyspace_id_type == keyrange_constants.KIT_BYTES:
      fmt = '%s'
      custom_sharding_key = pack_keyspace_id(custom_sharding_key)
    else:
      fmt = '%x'
    if should_be_here:
      self.assertEqual(result, ((mid, msg, custom_sharding_key),),
                       ('Bad row in tablet %s for id=%d, custom_sharding_key=' +
                        fmt + ', row=%s') % (tablet_obj.tablet_alias, mid,
                                             custom_sharding_key, str(result)))
    else:
      self.assertEqual(
          len(result), 0,
          ('Extra row in tablet %s for id=%d, custom_sharding_key=' +
           fmt + ': %s') % (tablet_obj.tablet_alias, mid, custom_sharding_key,
                            str(result)))

  # _is_value_present_and_correct tries to read a value.
  # if it is there, it will check it is correct and return True if it is.
  # if not correct, it will self.fail.
  # if not there, it will return False.
  def _is_value_present_and_correct(
      self, tablet_obj, table, mid, msg, custom_sharding_key):
    result = self._get_value(tablet_obj, table, mid)
    if not result:
      return False
    if keyspace_id_type == keyrange_constants.KIT_BYTES:
      fmt = '%s'
      custom_sharding_key = pack_keyspace_id(custom_sharding_key)
    else:
      fmt = '%x'
    self.assertEqual(result, ((mid, msg, custom_sharding_key),),
                     ('Bad row in tablet %s for id=%d, '
                      'custom_sharding_key=' + fmt) % (
                          tablet_obj.tablet_alias, mid, custom_sharding_key))
    return True

  def _insert_startup_values(self):
    self._insert_value(shard_0_master, 'resharding1', 1, 'msg1',
                       0x1000000000000000)
    self._insert_value(shard_1_master, 'resharding1', 2, 'msg2',
                       0x9000000000000000)
    self._insert_value(shard_1_master, 'resharding1', 3, 'msg3',
                       0xD000000000000000)

  def _check_startup_values(self):
    # check first value is in the right shard
    self._check_value(shard_2_master, 'resharding1', 2, 'msg2',
                      0x9000000000000000)
    self._check_value(shard_2_replica1, 'resharding1', 2, 'msg2',
                      0x9000000000000000)
    self._check_value(shard_2_replica2, 'resharding1', 2, 'msg2',
                      0x9000000000000000)
    self._check_value(shard_3_master, 'resharding1', 2, 'msg2',
                      0x9000000000000000, should_be_here=False)
    self._check_value(shard_3_replica, 'resharding1', 2, 'msg2',
                      0x9000000000000000, should_be_here=False)
    self._check_value(shard_3_rdonly1, 'resharding1', 2, 'msg2',
                      0x9000000000000000, should_be_here=False)

    # check second value is in the right shard too
    self._check_value(shard_2_master, 'resharding1', 3, 'msg3',
                      0xD000000000000000, should_be_here=False)
    self._check_value(shard_2_replica1, 'resharding1', 3, 'msg3',
                      0xD000000000000000, should_be_here=False)
    self._check_value(shard_2_replica2, 'resharding1', 3, 'msg3',
                      0xD000000000000000, should_be_here=False)
    self._check_value(shard_3_master, 'resharding1', 3, 'msg3',
                      0xD000000000000000)
    self._check_value(shard_3_replica, 'resharding1', 3, 'msg3',
                      0xD000000000000000)
    self._check_value(shard_3_rdonly1, 'resharding1', 3, 'msg3',
                      0xD000000000000000)

  def _insert_lots(self, count, base=0):
    for i in xrange(count):
      self._insert_value(shard_1_master, 'resharding1', 10000 + base + i,
                         'msg-range1-%d' % i, 0xA000000000000000 + base + i)
      self._insert_value(shard_1_master, 'resharding1', 20000 + base + i,
                         'msg-range2-%d' % i, 0xE000000000000000 + base + i)

  # _check_lots returns how many of the values we have, in percents.
  def _check_lots(self, count, base=0):
    found = 0
    for i in xrange(count):
      if self._is_value_present_and_correct(shard_2_replica2, 'resharding1',
                                            10000 + base + i, 'msg-range1-%d' %
                                            i, 0xA000000000000000 + base + i):
        found += 1
      if self._is_value_present_and_correct(shard_3_replica, 'resharding1',
                                            20000 + base + i, 'msg-range2-%d' %
                                            i, 0xE000000000000000 + base + i):
        found += 1
    percent = found * 100 / count / 2
    logging.debug('I have %d%% of the data', percent)
    return percent

  def _check_lots_timeout(self, count, threshold, timeout, base=0):
    while True:
      value = self._check_lots(count, base=base)
      if value >= threshold:
        return value
      timeout = utils.wait_step('waiting for %d%% of the data' % threshold,
                                timeout, sleep_time=1)

  # _check_lots_not_present makes sure no data is in the wrong shard
  def _check_lots_not_present(self, count, base=0):
    for i in xrange(count):
      self._check_value(shard_3_replica, 'resharding1', 10000 + base + i,
                        'msg-range1-%d' % i, 0xA000000000000000 + base + i,
                        should_be_here=False)
      self._check_value(shard_2_replica2, 'resharding1', 20000 + base + i,
                        'msg-range2-%d' % i, 0xE000000000000000 + base + i,
                        should_be_here=False)

  def _test_keyrange_constraints(self):
    with self.assertRaisesRegexp(
        Exception, '.*enforce custom_sharding_key range.*'):
      shard_0_master.execute(
          "insert into resharding1(id, msg, custom_sharding_key) "
          " values(1, 'msg', :custom_sharding_key)",
          bindvars={'custom_sharding_key': 0x9000000000000000},
      )
    with self.assertRaisesRegexp(
        Exception, '.*enforce custom_sharding_key range.*'):
      shard_0_master.execute(
          "update resharding1 set msg = 'msg' where id = 1",
          bindvars={'custom_sharding_key': 0x9000000000000000},
      )
    with self.assertRaisesRegexp(
        Exception, '.*enforce custom_sharding_key range.*'):
      shard_0_master.execute(
          'delete from resharding1 where id = 1',
          bindvars={'custom_sharding_key': 0x9000000000000000},
      )

  def test_resharding(self):
    # we're going to reparent and swap these two
    global shard_2_master, shard_2_replica1

    utils.run_vtctl(['CreateKeyspace',
                     '--sharding_column_name', 'bad_column',
                     '--sharding_column_type', 'bytes',
                     '--split_shard_count', '2',
                     'test_keyspace'])
    utils.run_vtctl(['SetKeyspaceShardingInfo', 'test_keyspace',
                     'custom_sharding_key', 'uint64'], expect_fail=True)
    utils.run_vtctl(['SetKeyspaceShardingInfo',
                     '-force', '-split_shard_count', '4',
                     'test_keyspace', 'custom_sharding_key', keyspace_id_type])

    shard_0_master.init_tablet('master', 'test_keyspace', '-80')
    shard_0_replica.init_tablet('replica', 'test_keyspace', '-80')
    shard_0_ny_rdonly.init_tablet('rdonly', 'test_keyspace', '-80')
    shard_1_master.init_tablet('master', 'test_keyspace', '80-')
    shard_1_slave1.init_tablet('replica', 'test_keyspace', '80-')
    shard_1_slave2.init_tablet('replica', 'test_keyspace', '80-')
    shard_1_ny_rdonly.init_tablet('rdonly', 'test_keyspace', '80-')
    shard_1_rdonly1.init_tablet('rdonly', 'test_keyspace', '80-')

    utils.run_vtctl(['RebuildKeyspaceGraph', 'test_keyspace'], auto_log=True)

    ks = utils.run_vtctl_json(['GetSrvKeyspace', 'test_nj', 'test_keyspace'])
    self.assertEqual(ks['split_shard_count'], 4)

    # we set full_mycnf_args to True as a test in the KIT_BYTES case
    full_mycnf_args = keyspace_id_type == keyrange_constants.KIT_BYTES

    # create databases so vttablet can start behaving normally
    for t in [shard_0_master, shard_0_replica, shard_0_ny_rdonly,
              shard_1_master, shard_1_slave1, shard_1_slave2, shard_1_ny_rdonly,
              shard_1_rdonly1]:
      t.create_db('vt_test_keyspace')
      t.start_vttablet(wait_for_state=None, full_mycnf_args=full_mycnf_args)

    # wait for the tablets
    shard_0_master.wait_for_vttablet_state('SERVING')
    shard_0_replica.wait_for_vttablet_state('SERVING')
    shard_0_ny_rdonly.wait_for_vttablet_state('SERVING')
    shard_1_master.wait_for_vttablet_state('SERVING')
    shard_1_slave1.wait_for_vttablet_state('SERVING')
    shard_1_slave2.wait_for_vttablet_state('SERVING')
    shard_1_ny_rdonly.wait_for_vttablet_state('SERVING')
    shard_1_rdonly1.wait_for_vttablet_state('SERVING')

    # reparent to make the tablets work
    utils.run_vtctl(['InitShardMaster', 'test_keyspace/-80',
                     shard_0_master.tablet_alias], auto_log=True)
    utils.run_vtctl(['InitShardMaster', 'test_keyspace/80-',
                     shard_1_master.tablet_alias], auto_log=True)

    # create the tables
    self._create_schema()
    self._insert_startup_values()
    self._test_keyrange_constraints()

    # run a health check on source replicas so they respond to discovery
    # (for binlog players) and on the source rdonlys (for workers)
    for t in [shard_0_replica, shard_1_slave1]:
      utils.run_vtctl(['RunHealthCheck', t.tablet_alias, 'replica'])
    for t in [shard_0_ny_rdonly, shard_1_ny_rdonly, shard_1_rdonly1]:
      utils.run_vtctl(['RunHealthCheck', t.tablet_alias, 'rdonly'])

    # create the split shards
    shard_2_master.init_tablet('master', 'test_keyspace', '80-c0')
    shard_2_replica1.init_tablet('replica', 'test_keyspace', '80-c0')
    shard_2_replica2.init_tablet('replica', 'test_keyspace', '80-c0')
    shard_3_master.init_tablet('master', 'test_keyspace', 'c0-')
    shard_3_replica.init_tablet('replica', 'test_keyspace', 'c0-')
    shard_3_rdonly1.init_tablet('rdonly', 'test_keyspace', 'c0-')

    # start vttablet on the split shards (no db created,
    # so they're all not serving)
    shard_3_master.start_vttablet(wait_for_state=None,
                                  target_tablet_type='replica')
    for t in [shard_2_master, shard_2_replica1, shard_2_replica2,
              shard_3_replica, shard_3_rdonly1]:
      t.start_vttablet(wait_for_state=None)
    for t in [shard_2_master, shard_2_replica1, shard_2_replica2,
              shard_3_master, shard_3_replica, shard_3_rdonly1]:
      t.wait_for_vttablet_state('NOT_SERVING')

    utils.run_vtctl(['InitShardMaster', 'test_keyspace/80-c0',
                     shard_2_master.tablet_alias], auto_log=True)
    utils.run_vtctl(['InitShardMaster', 'test_keyspace/c0-',
                     shard_3_master.tablet_alias], auto_log=True)

    utils.run_vtctl(['RebuildKeyspaceGraph', 'test_keyspace'],
                    auto_log=True)
    utils.check_srv_keyspace(
        'test_nj', 'test_keyspace',
        'Partitions(master): -80 80-\n'
        'Partitions(rdonly): -80 80-\n'
        'Partitions(replica): -80 80-\n',
        keyspace_id_type=keyspace_id_type,
        sharding_column_name='custom_sharding_key')

    # disable shard_1_slave2, so we're sure filtered replication will go
    # from shard_1_slave1
    utils.run_vtctl(['ChangeSlaveType', shard_1_slave2.tablet_alias, 'spare'])
    shard_1_slave2.wait_for_vttablet_state('NOT_SERVING')

    # the worker will do everything. We test with source_reader_count=10
    # (down from default=20) as connection pool is not big enough for 20.
    # min_table_size_for_split is set to 1 as to force a split even on the
    # small table we have.
    # we need to create the schema, and the worker will do data copying
    for keyspace_shard in ('test_keyspace/80-c0', 'test_keyspace/c0-'):
      utils.run_vtctl(['CopySchemaShard', '--exclude_tables', 'unrelated',
                       shard_1_rdonly1.tablet_alias, keyspace_shard],
                      auto_log=True)

    utils.run_vtworker(['--cell', 'test_nj',
                        '--command_display_interval', '10ms',
                        'SplitClone',
                        '--exclude_tables', 'unrelated',
                        '--source_reader_count', '10',
                        '--min_table_size_for_split', '1',
                        '--min_healthy_rdonly_endpoints', '1',
                        'test_keyspace/80-'],
                       auto_log=True)
    utils.run_vtctl(['ChangeSlaveType', shard_1_rdonly1.tablet_alias,
                     'rdonly'], auto_log=True)

    # TODO(alainjobart): experiment with the dontStartBinlogPlayer option

    # check the startup values are in the right place
    self._check_startup_values()

    # check the schema too
    utils.run_vtctl(['ValidateSchemaKeyspace', '--exclude_tables=unrelated',
                     'test_keyspace'], auto_log=True)

    # check the binlog players are running and exporting vars
    self.check_destination_master(shard_2_master, ['test_keyspace/80-'])
    self.check_destination_master(shard_3_master, ['test_keyspace/80-'])

    # check that binlog server exported the stats vars
    self.check_binlog_server_vars(shard_1_slave1, horizontal=True)

    # testing filtered replication: insert a bunch of data on shard 1,
    # check we get most of it after a few seconds, wait for binlog server
    # timeout, check we get all of it.
    logging.debug('Inserting lots of data on source shard')
    self._insert_lots(1000)
    logging.debug('Checking 80 percent of data is sent quickly')
    v = self._check_lots_timeout(1000, 80, 5)
    if v != 100:
      # small optimization: only do this check if we don't have all the data
      # already anyway.
      logging.debug('Checking all data goes through eventually')
      self._check_lots_timeout(1000, 100, 20)
    logging.debug('Checking no data was sent the wrong way')
    self._check_lots_not_present(1000)
    self.check_binlog_player_vars(shard_2_master, ['test_keyspace/80-'],
                                  seconds_behind_master_max=30)
    self.check_binlog_player_vars(shard_3_master, ['test_keyspace/80-'],
                                  seconds_behind_master_max=30)
    self.check_binlog_server_vars(shard_1_slave1, horizontal=True,
                                  min_statements=1000, min_transactions=1000)

    # use vtworker to compare the data (after health-checking the destination
    # rdonly tablets so discovery works)
    utils.run_vtctl(['RunHealthCheck', shard_3_rdonly1.tablet_alias, 'rdonly'])
    logging.debug('Running vtworker SplitDiff')
    utils.run_vtworker(['-cell', 'test_nj', 'SplitDiff',
                        '--exclude_tables', 'unrelated',
                        '--min_healthy_rdonly_endpoints', '1',
                        'test_keyspace/c0-'],
                       auto_log=True)
    utils.run_vtctl(['ChangeSlaveType', shard_1_rdonly1.tablet_alias, 'rdonly'],
                    auto_log=True)
    utils.run_vtctl(['ChangeSlaveType', shard_3_rdonly1.tablet_alias, 'rdonly'],
                    auto_log=True)

    utils.pause('Good time to test vtworker for diffs')

    # get status for destination master tablets, make sure we have it all
    self.check_running_binlog_player(shard_2_master, 4000, 2000)
    self.check_running_binlog_player(shard_3_master, 4000, 2000)

    # start a thread to insert data into shard_1 in the background
    # with current time, and monitor the delay
    insert_thread_1 = InsertThread(shard_1_master, 'insert_low', 10000,
                                   0x9000000000000000)
    insert_thread_2 = InsertThread(shard_1_master, 'insert_high', 10001,
                                   0xD000000000000000)
    monitor_thread_1 = MonitorLagThread(shard_2_replica2, 'insert_low')
    monitor_thread_2 = MonitorLagThread(shard_3_replica, 'insert_high')

    # tests a failover switching serving to a different replica
    utils.run_vtctl(['ChangeSlaveType', shard_1_slave2.tablet_alias, 'replica'])
    utils.run_vtctl(['ChangeSlaveType', shard_1_slave1.tablet_alias, 'spare'])
    shard_1_slave2.wait_for_vttablet_state('SERVING')
    shard_1_slave1.wait_for_vttablet_state('NOT_SERVING')
    utils.run_vtctl(['RunHealthCheck', shard_1_slave2.tablet_alias, 'replica'])

    # test data goes through again
    logging.debug('Inserting lots of data on source shard')
    self._insert_lots(1000, base=1000)
    logging.debug('Checking 80 percent of data was sent quickly')
    self._check_lots_timeout(1000, 80, 5, base=1000)
    self.check_binlog_server_vars(shard_1_slave2, horizontal=True,
                                  min_statements=800, min_transactions=800)

    # check we can't migrate the master just yet
    utils.run_vtctl(['MigrateServedTypes', 'test_keyspace/80-', 'master'],
                    expect_fail=True)

    # check query service is off on master 2 and master 3, as filtered
    # replication is enabled. Even health check that is enabled on
    # master 3 should not interfere (we run it to be sure).
    utils.run_vtctl(['RunHealthCheck', shard_3_master.tablet_alias, 'replica'],
                    auto_log=True)
    for master in [shard_2_master, shard_3_master]:
      utils.check_tablet_query_service(self, master, False, False)
      stream_health = utils.run_vtctl_json(['VtTabletStreamHealth',
                                            '-count', '1',
                                            master.tablet_alias])
      logging.debug('Got health: %s', str(stream_health))
      self.assertIn('realtime_stats', stream_health)
      self.assertNotIn('serving', stream_health)

    # check the destination master 3 is healthy, even though its query
    # service is not running (if not healthy this would exception out)
    shard_3_master.get_healthz()

    # now serve rdonly from the split shards, in test_nj only
    utils.run_vtctl(['MigrateServedTypes', '--cells=test_nj',
                     'test_keyspace/80-', 'rdonly'], auto_log=True)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_srv_keyspace('test_ny', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-\n'
                             'Partitions(replica): -80 80-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_tablet_query_service(self, shard_0_ny_rdonly, True, False)
    utils.check_tablet_query_service(self, shard_1_ny_rdonly, True, False)
    utils.check_tablet_query_service(self, shard_1_rdonly1, False, True)

    # now serve rdonly from the split shards, everywhere
    utils.run_vtctl(['MigrateServedTypes', 'test_keyspace/80-', 'rdonly'],
                    auto_log=True)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_srv_keyspace('test_ny', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_tablet_query_service(self, shard_0_ny_rdonly, True, False)
    utils.check_tablet_query_service(self, shard_1_ny_rdonly, False, True)
    utils.check_tablet_query_service(self, shard_1_rdonly1, False, True)

    # then serve replica from the split shards
    destination_shards = ['test_keyspace/80-c0', 'test_keyspace/c0-']

    utils.run_vtctl(['MigrateServedTypes', 'test_keyspace/80-', 'replica'],
                    auto_log=True)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-c0 c0-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_tablet_query_service(self, shard_1_slave2, False, True)

    # move replica back and forth
    utils.run_vtctl(
        ['MigrateServedTypes', '-reverse', 'test_keyspace/80-', 'replica'],
        auto_log=True)
    # After a backwards migration, queryservice should be enabled on
    # source and disabled on destinations
    utils.check_tablet_query_service(self, shard_1_slave2, True, False)
    # Destination tablets would have query service disabled for other
    # reasons than the migration, so check the shard record instead of
    # the tablets directly.
    utils.check_shard_query_services(self, destination_shards,
                                     topodata_pb2.REPLICA, False)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')

    utils.run_vtctl(['MigrateServedTypes', 'test_keyspace/80-', 'replica'],
                    auto_log=True)
    # After a forwards migration, queryservice should be disabled on
    # source and enabled on destinations
    utils.check_tablet_query_service(self, shard_1_slave2, False, True)
    # Destination tablets would have query service disabled for other
    # reasons than the migration, so check the shard record instead of
    # the tablets directly
    utils.check_shard_query_services(self, destination_shards,
                                     topodata_pb2.REPLICA, True)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-c0 c0-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')

    # reparent shard_2 to shard_2_replica1, then insert more data and
    # see it flow through still
    utils.run_vtctl(['PlannedReparentShard', 'test_keyspace/80-c0',
                     shard_2_replica1.tablet_alias])

    # update our test variables to point at the new master
    shard_2_master, shard_2_replica1 = shard_2_replica1, shard_2_master

    logging.debug('Inserting lots of data on source shard after reparenting')
    self._insert_lots(3000, base=2000)
    logging.debug('Checking 80 percent of data was sent fairly quickly')
    self._check_lots_timeout(3000, 80, 10, base=2000)

    # use vtworker to compare the data again
    logging.debug('Running vtworker SplitDiff')
    utils.run_vtworker(['-cell', 'test_nj', 'SplitDiff',
                        '--exclude_tables', 'unrelated',
                        '--min_healthy_rdonly_endpoints', '1',
                        'test_keyspace/c0-'],
                       auto_log=True)
    utils.run_vtctl(['ChangeSlaveType', shard_1_rdonly1.tablet_alias, 'rdonly'],
                    auto_log=True)
    utils.run_vtctl(['ChangeSlaveType', shard_3_rdonly1.tablet_alias, 'rdonly'],
                    auto_log=True)

    # going to migrate the master now, check the delays
    monitor_thread_1.done = True
    monitor_thread_2.done = True
    insert_thread_1.done = True
    insert_thread_2.done = True
    logging.debug('DELAY 1: %s max_lag=%d avg_lag=%d',
                  monitor_thread_1.object_name,
                  monitor_thread_1.max_lag,
                  monitor_thread_1.lag_sum / monitor_thread_1.sample_count)
    logging.debug('DELAY 2: %s max_lag=%d avg_lag=%d',
                  monitor_thread_2.object_name,
                  monitor_thread_2.max_lag,
                  monitor_thread_2.lag_sum / monitor_thread_2.sample_count)

    # mock with the SourceShard records to test 'vtctl SourceShardDelete'
    # and 'vtctl SourceShardAdd'
    utils.run_vtctl(['SourceShardDelete', 'test_keyspace/c0-', '0'],
                    auto_log=True)
    utils.run_vtctl(['SourceShardAdd', '--key_range=80-',
                     'test_keyspace/c0-', '0', 'test_keyspace/80-'],
                    auto_log=True)

    # then serve master from the split shards, make sure the source master's
    # query service is now turned off
    utils.run_vtctl(['MigrateServedTypes', 'test_keyspace/80-', 'master'],
                    auto_log=True)
    utils.check_srv_keyspace('test_nj', 'test_keyspace',
                             'Partitions(master): -80 80-c0 c0-\n'
                             'Partitions(rdonly): -80 80-c0 c0-\n'
                             'Partitions(replica): -80 80-c0 c0-\n',
                             keyspace_id_type=keyspace_id_type,
                             sharding_column_name='custom_sharding_key')
    utils.check_tablet_query_service(self, shard_1_master, False, True)

    # check the binlog players are gone now
    self.check_no_binlog_player(shard_2_master)
    self.check_no_binlog_player(shard_3_master)

    # delete the original tablets in the original shard
    tablet.kill_tablets([shard_1_master, shard_1_slave1, shard_1_slave2,
                         shard_1_ny_rdonly, shard_1_rdonly1])
    for t in [shard_1_slave1, shard_1_slave2, shard_1_ny_rdonly,
              shard_1_rdonly1]:
      utils.run_vtctl(['DeleteTablet', t.tablet_alias], auto_log=True)
    utils.run_vtctl(['DeleteTablet', '-allow_master',
                     shard_1_master.tablet_alias], auto_log=True)

    # rebuild the serving graph, all mentions of the old shards shoud be gone
    utils.run_vtctl(
        ['RebuildKeyspaceGraph', 'test_keyspace'], auto_log=True)

    # test RemoveShardCell
    utils.run_vtctl(
        ['RemoveShardCell', 'test_keyspace/-80', 'test_nj'], auto_log=True,
        expect_fail=True)
    utils.run_vtctl(
        ['RemoveShardCell', 'test_keyspace/80-', 'test_nj'], auto_log=True)
    utils.run_vtctl(
        ['RemoveShardCell', 'test_keyspace/80-', 'test_ny'], auto_log=True)
    shard = utils.run_vtctl_json(['GetShard', 'test_keyspace/80-'])
    self.assertNotIn('cells', shard)

    # delete the original shard
    utils.run_vtctl(['DeleteShard', 'test_keyspace/80-'], auto_log=True)

    # kill everything
    tablet.kill_tablets([shard_0_master, shard_0_replica, shard_0_ny_rdonly,
                         shard_2_master, shard_2_replica1, shard_2_replica2,
                         shard_3_master, shard_3_replica, shard_3_rdonly1])

if __name__ == '__main__':
  utils.main()
