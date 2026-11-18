use base64::{engine::general_purpose::STANDARD, Engine as _};

const ZSTD_LEVEL: i32 = 3;

/// Zstd compress (level 3) + base64 encode.
pub fn compress_and_encode(input: &str) -> String {
    let compressed = zstd::encode_all(input.as_bytes(), ZSTD_LEVEL)
        .unwrap_or_else(|_| input.as_bytes().to_vec());
    STANDARD.encode(&compressed)
}

/// Base64 decode + zstd decompress.
pub fn decode_and_decompress(encoded: &str) -> Result<String, String> {
    let bytes = STANDARD
        .decode(encoded)
        .map_err(|e| format!("base64: {}", e))?;
    let decompressed = zstd::decode_all(bytes.as_slice()).map_err(|e| format!("zstd: {}", e))?;
    String::from_utf8(decompressed).map_err(|e| format!("utf8: {}", e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_roundtrip() {
        let original = "hello world, this is a test of zstd compression";
        let encoded = compress_and_encode(original);
        let decoded = decode_and_decompress(&encoded).unwrap();
        assert_eq!(decoded, original);
    }

    #[test]
    fn test_empty_string() {
        let encoded = compress_and_encode("");
        let decoded = decode_and_decompress(&encoded).unwrap();
        assert_eq!(decoded, "");
    }

    #[test]
    fn test_large_input() {
        let original = "diff --git a/file.rs\n".repeat(10_000);
        let encoded = compress_and_encode(&original);
        assert!(
            encoded.len() < original.len(),
            "compression should reduce size"
        );
        let decoded = decode_and_decompress(&encoded).unwrap();
        assert_eq!(decoded, original);
    }
}
