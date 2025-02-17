// Copyright 2015, Google Inc. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package sqltypes

import (
	"fmt"

	querypb "github.com/youtube/vitess/go/vt/proto/query"
)

// This file provides wrappers and support
// functions for querypb.Type.

// These bit flags can be used to query on the
// common properties of types.
const (
	flagIsIntegral = int(querypb.Flag_ISINTEGRAL)
	flagIsUnsigned = int(querypb.Flag_ISUNSIGNED)
	flagIsFloat    = int(querypb.Flag_ISFLOAT)
	flagIsQuoted   = int(querypb.Flag_ISQUOTED)
	flagIsText     = int(querypb.Flag_ISTEXT)
	flagIsBinary   = int(querypb.Flag_ISBINARY)
)

// IsIntegral returns true if querypb.Type is an integral
// (signed/unsigned) that can be represented using
// up to 64 binary bits.
func IsIntegral(t querypb.Type) bool {
	return int(t)&flagIsIntegral == flagIsIntegral
}

// IsSigned returns true if querypb.Type is a signed integral.
func IsSigned(t querypb.Type) bool {
	return int(t)&(flagIsIntegral|flagIsUnsigned) == flagIsIntegral
}

// IsUnsigned returns true if querypb.Type is an unsigned integral.
// Caution: this is not the same as !IsSigned.
func IsUnsigned(t querypb.Type) bool {
	return int(t)&(flagIsIntegral|flagIsUnsigned) == flagIsIntegral|flagIsUnsigned
}

// IsFloat returns true is querypb.Type is a floating point.
func IsFloat(t querypb.Type) bool {
	return int(t)&flagIsFloat == flagIsFloat
}

// IsQuoted returns true if querypb.Type is a quoted text or binary.
func IsQuoted(t querypb.Type) bool {
	return int(t)&flagIsQuoted == flagIsQuoted
}

// IsText returns true if querypb.Type is a text.
func IsText(t querypb.Type) bool {
	return int(t)&flagIsText == flagIsText
}

// IsBinary returns true if querypb.Type is a binary.
func IsBinary(t querypb.Type) bool {
	return int(t)&flagIsBinary == flagIsBinary
}

// Vitess data types. These are idiomatically
// named synonyms for the querypb.Type values.
const (
	Null      = querypb.Type_NULL_TYPE
	Int8      = querypb.Type_INT8
	Uint8     = querypb.Type_UINT8
	Int16     = querypb.Type_INT16
	Uint16    = querypb.Type_UINT16
	Int24     = querypb.Type_INT24
	Uint24    = querypb.Type_UINT24
	Int32     = querypb.Type_INT32
	Uint32    = querypb.Type_UINT32
	Int64     = querypb.Type_INT64
	Uint64    = querypb.Type_UINT64
	Float32   = querypb.Type_FLOAT32
	Float64   = querypb.Type_FLOAT64
	Timestamp = querypb.Type_TIMESTAMP
	Date      = querypb.Type_DATE
	Time      = querypb.Type_TIME
	Datetime  = querypb.Type_DATETIME
	Year      = querypb.Type_YEAR
	Decimal   = querypb.Type_DECIMAL
	Text      = querypb.Type_TEXT
	Blob      = querypb.Type_BLOB
	VarChar   = querypb.Type_VARCHAR
	VarBinary = querypb.Type_VARBINARY
	Char      = querypb.Type_CHAR
	Binary    = querypb.Type_BINARY
	Bit       = querypb.Type_BIT
	Enum      = querypb.Type_ENUM
	Set       = querypb.Type_SET
	Tuple     = querypb.Type_TUPLE
)

// bit-shift the mysql flags by two byte so we
// can merge them with the mysql or vitess types.
const (
	mysqlUnsigned = 32 << 16
	mysqlBinary   = 128 << 16
	mysqlEnum     = 256 << 16
	mysqlSet      = 2048 << 16

	relevantFlags = mysqlUnsigned |
		mysqlBinary |
		mysqlEnum |
		mysqlSet
)

// If you add to this map, make sure you add a test case
// in tabletserver/endtoend.
var mysqlToType = map[int64]querypb.Type{
	1:   Int8,
	2:   Int16,
	3:   Int32,
	4:   Float32,
	5:   Float64,
	6:   Null,
	7:   Timestamp,
	8:   Int64,
	9:   Int24,
	10:  Date,
	11:  Time,
	12:  Datetime,
	13:  Year,
	16:  Bit,
	246: Decimal,
	249: Text,
	250: Text,
	251: Text,
	252: Text,
	253: VarChar,
	254: Char,
}

var modifier = map[int64]querypb.Type{
	int64(Int8) | mysqlUnsigned:  Uint8,
	int64(Int16) | mysqlUnsigned: Uint16,
	int64(Int32) | mysqlUnsigned: Uint32,
	int64(Int64) | mysqlUnsigned: Uint64,
	int64(Int24) | mysqlUnsigned: Uint24,
	int64(Text) | mysqlBinary:    Blob,
	int64(VarChar) | mysqlBinary: VarBinary,
	int64(Char) | mysqlBinary:    Binary,
	int64(Char) | mysqlEnum:      Enum,
	int64(Char) | mysqlSet:       Set,
}

// typeToMySQL is the reverse of mysqlToType.
var typeToMySQL = map[querypb.Type]struct {
	typ   int64
	flags int64
}{
	Int8:      {typ: 1},
	Uint8:     {typ: 1, flags: mysqlUnsigned},
	Int16:     {typ: 2},
	Uint16:    {typ: 2, flags: mysqlUnsigned},
	Int32:     {typ: 3},
	Uint32:    {typ: 3, flags: mysqlUnsigned},
	Float32:   {typ: 4},
	Float64:   {typ: 5},
	Null:      {typ: 6, flags: mysqlBinary},
	Timestamp: {typ: 7},
	Int64:     {typ: 8},
	Uint64:    {typ: 8, flags: mysqlUnsigned},
	Int24:     {typ: 9},
	Uint24:    {typ: 9, flags: mysqlUnsigned},
	Date:      {typ: 10, flags: mysqlBinary},
	Time:      {typ: 11, flags: mysqlBinary},
	Datetime:  {typ: 12, flags: mysqlBinary},
	Year:      {typ: 13, flags: mysqlUnsigned},
	Bit:       {typ: 16, flags: mysqlUnsigned},
	Decimal:   {typ: 246},
	Text:      {typ: 252},
	Blob:      {typ: 252, flags: mysqlBinary},
	VarChar:   {typ: 253},
	VarBinary: {typ: 253, flags: mysqlBinary},
	Char:      {typ: 254},
	Binary:    {typ: 254, flags: mysqlBinary},
	Enum:      {typ: 254, flags: mysqlEnum},
	Set:       {typ: 254, flags: mysqlSet},
}

// MySQLToType computes the vitess type from mysql type and flags.
func MySQLToType(mysqlType, flags int64) (typ querypb.Type, err error) {
	result, ok := mysqlToType[mysqlType]
	if !ok {
		return 0, fmt.Errorf("unsupported type: %d", mysqlType)
	}

	converted := (flags << 16) & relevantFlags
	modified, ok := modifier[int64(result)|converted]
	if ok {
		return modified, nil
	}
	return result, nil
}

// TypeToMySQL returns the equivalent mysql type and flag for a vitess type.
func TypeToMySQL(typ querypb.Type) (mysqlType, flags int64) {
	val := typeToMySQL[typ]
	return val.typ, val.flags >> 16
}
