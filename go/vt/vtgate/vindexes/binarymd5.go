package vindexes

import (
	"bytes"
	"crypto/md5"
	"fmt"

	"github.com/youtube/vitess/go/sqltypes"
)

// BinaryMD5 is a vindex that hashes binary bits to a keyspace id.
type BinaryMD5 struct {
	name string
}

// NewBinaryMD5 creates a new BinaryMD5.
func NewBinaryMD5(name string, _ map[string]interface{}) (Vindex, error) {
	return &BinaryMD5{name: name}, nil
}

// String returns the name of the vindex.
func (vind *BinaryMD5) String() string {
	return vind.name
}

// Cost returns the cost as 1.
func (vind *BinaryMD5) Cost() int {
	return 1
}

// Verify returns true if id maps to ksid.
func (vind *BinaryMD5) Verify(_ VCursor, id interface{}, ksid []byte) (bool, error) {
	data, err := binHashKey(id)
	if err != nil {
		return false, fmt.Errorf("BinaryMD5_hash.Verify: %v", err)
	}
	return bytes.Compare(data, ksid) == 0, nil
}

// Map returns the corresponding keyspace id values for the given ids.
func (vind *BinaryMD5) Map(_ VCursor, ids []interface{}) ([][]byte, error) {
	out := make([][]byte, 0, len(ids))
	for _, id := range ids {
		data, err := binHashKey(id)
		if err != nil {
			return nil, fmt.Errorf("BinaryMd5.Map :%v", err)
		}
		out = append(out, data)
	}
	return out, nil
}

func binHashKey(key interface{}) ([]byte, error) {
	source, err := getBytes(key)
	if err != nil {
		return nil, err
	}
	return binHash(source), nil
}

func getBytes(key interface{}) ([]byte, error) {
	switch v := key.(type) {
	case []byte:
		return v, nil
	case sqltypes.Value:
		return v.Raw(), nil
	}
	return nil, fmt.Errorf("unexpected data type for binHash: %T", key)
}

func binHash(source []byte) []byte {
	sum := md5.Sum(source)
	return sum[:]
}

func init() {
	Register("binary_md5", NewBinaryMD5)
}
