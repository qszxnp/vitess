// Copyright 2012, Google Inc. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package proto

// DO NOT EDIT.
// FILE GENERATED BY BSONGEN.

import (
	"bytes"

	"github.com/youtube/vitess/go/bson"
	"github.com/youtube/vitess/go/bytes2"
	tproto "github.com/youtube/vitess/go/vt/tabletserver/proto"
)

// MarshalBson bson-encodes KeyspaceIdBatchQuery.
func (keyspaceIdBatchQuery *KeyspaceIdBatchQuery) MarshalBson(buf *bytes2.ChunkedWriter, key string) {
	bson.EncodeOptionalPrefix(buf, bson.Object, key)
	lenWriter := bson.NewLenWriter(buf)

	// *tproto.CallerID
	if keyspaceIdBatchQuery.CallerID == nil {
		bson.EncodePrefix(buf, bson.Null, "CallerID")
	} else {
		(*keyspaceIdBatchQuery.CallerID).MarshalBson(buf, "CallerID")
	}
	// []BoundKeyspaceIdQuery
	{
		bson.EncodePrefix(buf, bson.Array, "Queries")
		lenWriter := bson.NewLenWriter(buf)
		for _i, _v1 := range keyspaceIdBatchQuery.Queries {
			_v1.MarshalBson(buf, bson.Itoa(_i))
		}
		lenWriter.Close()
	}
	keyspaceIdBatchQuery.TabletType.MarshalBson(buf, "TabletType")
	bson.EncodeBool(buf, "AsTransaction", keyspaceIdBatchQuery.AsTransaction)
	// *Session
	if keyspaceIdBatchQuery.Session == nil {
		bson.EncodePrefix(buf, bson.Null, "Session")
	} else {
		(*keyspaceIdBatchQuery.Session).MarshalBson(buf, "Session")
	}

	lenWriter.Close()
}

// UnmarshalBson bson-decodes into KeyspaceIdBatchQuery.
func (keyspaceIdBatchQuery *KeyspaceIdBatchQuery) UnmarshalBson(buf *bytes.Buffer, kind byte) {
	switch kind {
	case bson.EOO, bson.Object:
		// valid
	case bson.Null:
		return
	default:
		panic(bson.NewBsonError("unexpected kind %v for KeyspaceIdBatchQuery", kind))
	}
	bson.Next(buf, 4)

	for kind := bson.NextByte(buf); kind != bson.EOO; kind = bson.NextByte(buf) {
		switch bson.ReadCString(buf) {
		case "CallerID":
			// *tproto.CallerID
			if kind != bson.Null {
				keyspaceIdBatchQuery.CallerID = new(tproto.CallerID)
				(*keyspaceIdBatchQuery.CallerID).UnmarshalBson(buf, kind)
			}
		case "Queries":
			// []BoundKeyspaceIdQuery
			if kind != bson.Null {
				if kind != bson.Array {
					panic(bson.NewBsonError("unexpected kind %v for keyspaceIdBatchQuery.Queries", kind))
				}
				bson.Next(buf, 4)
				keyspaceIdBatchQuery.Queries = make([]BoundKeyspaceIdQuery, 0, 8)
				for kind := bson.NextByte(buf); kind != bson.EOO; kind = bson.NextByte(buf) {
					bson.SkipIndex(buf)
					var _v1 BoundKeyspaceIdQuery
					_v1.UnmarshalBson(buf, kind)
					keyspaceIdBatchQuery.Queries = append(keyspaceIdBatchQuery.Queries, _v1)
				}
			}
		case "TabletType":
			keyspaceIdBatchQuery.TabletType.UnmarshalBson(buf, kind)
		case "AsTransaction":
			keyspaceIdBatchQuery.AsTransaction = bson.DecodeBool(buf, kind)
		case "Session":
			// *Session
			if kind != bson.Null {
				keyspaceIdBatchQuery.Session = new(Session)
				(*keyspaceIdBatchQuery.Session).UnmarshalBson(buf, kind)
			}
		default:
			bson.Skip(buf, kind)
		}
	}
}