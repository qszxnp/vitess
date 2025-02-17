// Copyright 2012, Google Inc. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package tabletconn

import (
	"flag"
	"time"

	log "github.com/golang/glog"
	"github.com/youtube/vitess/go/sqltypes"
	"golang.org/x/net/context"

	querypb "github.com/youtube/vitess/go/vt/proto/query"
	topodatapb "github.com/youtube/vitess/go/vt/proto/topodata"
	vtrpcpb "github.com/youtube/vitess/go/vt/proto/vtrpc"
	"github.com/youtube/vitess/go/vt/tabletserver/querytypes"
)

const (
	// ConnClosed is returned when the underlying connection was closed.
	ConnClosed = OperationalError("vttablet: Connection Closed")

	// Cancelled can be returned if the user canceled the request.
	// FIXME(alainjobart): this seems wrong, if anything, we should rely
	// on context being canceled, or just use context.Canceled.
	Cancelled = OperationalError("vttablet: Context Cancelled")
)

var (
	// TabletProtocol is exported for unit tests
	TabletProtocol = flag.String("tablet_protocol", "grpc", "how to talk to the vttablets")
)

// ServerError represents an error that was returned from
// a vttablet server. it implements vterrors.VtError.
type ServerError struct {
	Err string
	// ServerCode is the error code that we got from the server.
	ServerCode vtrpcpb.ErrorCode
}

func (e *ServerError) Error() string { return e.Err }

// VtErrorCode returns the underlying Vitess error code.
// This makes ServerError implement vterrors.VtError.
func (e *ServerError) VtErrorCode() vtrpcpb.ErrorCode { return e.ServerCode }

// OperationalError represents an error due to a failure to
// communicate with vttablet.
type OperationalError string

func (e OperationalError) Error() string { return string(e) }

// StreamHealthReader defines the interface for a reader to read StreamHealth messages.
type StreamHealthReader interface {
	// Recv reads one StreamHealthResponse.
	Recv() (*querypb.StreamHealthResponse, error)
}

// In all the following calls, context is an opaque structure that may
// carry data related to the call. For instance, if an incoming RPC
// call is responsible for these outgoing calls, and the incoming
// protocol and outgoing protocols support forwarding information, use
// context.

// TabletDialer represents a function that will return a TabletConn
// object that can communicate with a tablet.
//
// keyspace, shard and tabletType are remembered and used as Target.
// Use SetTarget to update them later.
// If the TabletDialer is used for StreamHealth only, then keyspace, shard
// and tabletType won't be used.
type TabletDialer func(ctx context.Context, endPoint *topodatapb.EndPoint, keyspace, shard string, tabletType topodatapb.TabletType, timeout time.Duration) (TabletConn, error)

// TabletConn defines the interface for a vttablet client. It should
// not be concurrently used across goroutines.
type TabletConn interface {
	// Execute executes a non-streaming query on vttablet.
	Execute(ctx context.Context, query string, bindVars map[string]interface{}, transactionID int64) (*sqltypes.Result, error)

	// ExecuteBatch executes a group of queries.
	ExecuteBatch(ctx context.Context, queries []querytypes.BoundQuery, asTransaction bool, transactionID int64) ([]sqltypes.Result, error)

	// StreamExecute executes a streaming query on vttablet. It
	// returns a sqltypes.ResultStream to get results from. If
	// error is non-nil, it means that the StreamExecute failed to
	// send the request. Otherwise, you can pull values from the
	// ResultStream until io.EOF, or any other error.
	StreamExecute(ctx context.Context, query string, bindVars map[string]interface{}) (sqltypes.ResultStream, error)

	// Transaction support
	Begin(ctx context.Context) (transactionID int64, err error)
	Commit(ctx context.Context, transactionID int64) error
	Rollback(ctx context.Context, transactionID int64) error

	// Combo RPC calls: they execute both a Begin and another call.
	// Note even if error is set, transactionID may be returned
	// and different than zero, if the Begin part worked.
	BeginExecute(ctx context.Context, query string, bindVars map[string]interface{}) (result *sqltypes.Result, transactionID int64, err error)
	BeginExecuteBatch(ctx context.Context, queries []querytypes.BoundQuery, asTransaction bool) (results []sqltypes.Result, transactionID int64, err error)

	// Close must be called for releasing resources.
	Close()

	// SetTarget can be called to change the target used for
	// subsequent calls.
	SetTarget(keyspace, shard string, tabletType topodatapb.TabletType) error

	// GetEndPoint returns the end point info.
	EndPoint() *topodatapb.EndPoint

	// SplitQuery splits a query into equally sized smaller queries by
	// appending primary key range clauses to the original query
	SplitQuery(ctx context.Context, query querytypes.BoundQuery, splitColumn string, splitCount int64) ([]querytypes.QuerySplit, error)

	// SplitQuery splits a query into equally sized smaller queries by
	// appending primary key range clauses to the original query
	// TODO(erez): Remove SplitQuery and rename this to SplitQueryV2 once migration is done.
	SplitQueryV2(
		ctx context.Context,
		query querytypes.BoundQuery,
		splitColumns []string,
		splitCount int64,
		numRowsPerQueryPart int64,
		algorithm querypb.SplitQueryRequest_Algorithm) (queries []querytypes.QuerySplit, err error)

	// StreamHealth starts a streaming RPC for VTTablet health status updates.
	StreamHealth(ctx context.Context) (StreamHealthReader, error)
}

var dialers = make(map[string]TabletDialer)

// RegisterDialer is meant to be used by TabletDialer implementations
// to self register.
func RegisterDialer(name string, dialer TabletDialer) {
	if _, ok := dialers[name]; ok {
		log.Fatalf("Dialer %s already exists", name)
	}
	dialers[name] = dialer
}

// GetDialer returns the dialer to use, described by the command line flag
func GetDialer() TabletDialer {
	td, ok := dialers[*TabletProtocol]
	if !ok {
		log.Fatalf("No dialer registered for tablet protocol %s", *TabletProtocol)
	}
	return td
}
