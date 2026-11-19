// Copyright The OpenTelemetry Authors
// Copyright 2017 gRPC authors
// SPDX-License-Identifier: Apache-2.0

package internal // import "go.opentelemetry.io/collector/config/configgrpc/internal"

import (
	"io"
	"sync"

	"github.com/klauspost/compress/zstd"
	"google.golang.org/grpc/encoding"
)

const ZstdName = "zstd"

func init() {
	encoding.RegisterCompressor(NewZstdCodec())
}

type writer struct {
	*zstd.Encoder
	pool *sync.Pool
}

func NewZstdCodec() encoding.Compressor {
	c := &compressor{}
	c.poolCompressor.New = func() any {
		zw, _ := zstd.NewWriter(nil, zstd.WithEncoderConcurrency(1), zstd.WithWindowSize(512*1024))
		return &writer{Encoder: zw, pool: &c.poolCompressor}
	}
	return c
}

func (c *compressor) Compress(w io.Writer) (io.WriteCloser, error) {
	z := c.poolCompressor.Get().(*writer)
	z.Encoder.Reset(w)
	return z, nil
}

func (z *writer) Close() error {
	defer z.pool.Put(z)
	return z.Encoder.Close()
}

type reader struct {
	decoder *zstd.Decoder
	pool    *sync.Pool
}

func (c *compressor) Decompress(input io.Reader) (io.Reader, error) {
	decoder, _ := c.poolDecompressor.Get().(*zstd.Decoder)
	var err error
	if decoder == nil {
		// Keep decoding synchronous, as in the previous gateway codec.
		decoder, err = zstd.NewReader(input, zstd.WithDecoderConcurrency(1))
	} else {
		err = decoder.Reset(input)
	}
	if err != nil {
		if decoder != nil {
			decoder.Close()
		}
		return nil, err
	}
	return &reader{decoder: decoder, pool: &c.poolDecompressor}, nil
}

func (r *reader) Read(p []byte) (int, error) {
	if r.decoder == nil {
		return 0, io.EOF
	}
	return r.decoder.Read(p)
}

// gRPC closes the decoder even when it stops reading before EOF.
func (r *reader) Close() error {
	decoder := r.decoder
	if decoder == nil {
		return nil
	}
	r.decoder = nil
	if err := decoder.Reset(nil); err != nil {
		decoder.Close()
		return err
	}
	r.pool.Put(decoder)
	return nil
}

func (c *compressor) Name() string {
	return ZstdName
}

type compressor struct {
	poolCompressor   sync.Pool
	poolDecompressor sync.Pool
}
