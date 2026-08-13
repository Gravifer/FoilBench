//! Minimal little-endian `NumPy` `.npy` v1/v2 reader and C-order writer.

#![allow(clippy::cast_possible_truncation)]

use std::{fs, path::Path};

use foilbench_core::{FlowScalar, Precision};

#[derive(Clone, Debug, PartialEq)]
pub struct NpyArray {
    pub precision: Precision,
    pub shape: Vec<usize>,
    pub fortran_order: bool,
    pub values: Vec<f64>,
}

fn parse_header(header: &str) -> Result<(Precision, bool, Vec<usize>), String> {
    let precision = if header.contains("'<f4'") || header.contains("'|f4'") {
        Precision::Float32
    } else if header.contains("'<f8'") || header.contains("'|f8'") {
        Precision::Float64
    } else {
        return Err("unsupported NPY dtype; expected little-endian float32/float64".into());
    };
    let fortran_order = if header.contains("'fortran_order': True") {
        true
    } else if header.contains("'fortran_order': False") {
        false
    } else {
        return Err("NPY header lacks a Boolean fortran_order".into());
    };
    let shape_start = header.find("'shape': (").ok_or("NPY header lacks shape")? + 10;
    let shape_end = header[shape_start..]
        .find(')')
        .ok_or("NPY shape is unterminated")?
        + shape_start;
    let shape = header[shape_start..shape_end]
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.parse::<usize>().map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    if shape.is_empty() || shape.contains(&0) {
        return Err("NPY shape must contain positive dimensions".into());
    }
    Ok((precision, fortran_order, shape))
}

fn fortran_to_c(values: &[f64], shape: &[usize]) -> Vec<f64> {
    let mut output = vec![0.0; values.len()];
    for (c_index, selected) in output.iter_mut().enumerate() {
        let mut remainder = c_index;
        let mut coordinates = vec![0; shape.len()];
        for axis in (0..shape.len()).rev() {
            coordinates[axis] = remainder % shape[axis];
            remainder /= shape[axis];
        }
        let mut f_index = 0;
        let mut stride = 1;
        for (coordinate, size) in coordinates.iter().zip(shape) {
            f_index += coordinate * stride;
            stride *= size;
        }
        *selected = values[f_index];
    }
    output
}

/// Read and semantically normalize a `NumPy` array to C-order values.
///
/// # Errors
///
/// Returns an error for malformed, unsupported, truncated, or non-finite arrays.
pub fn read_npy(path: &Path) -> Result<NpyArray, String> {
    let bytes = fs::read(path).map_err(|error| error.to_string())?;
    if bytes.len() < 10 || &bytes[..6] != b"\x93NUMPY" {
        return Err("invalid NPY magic".into());
    }
    let (header_length, prefix) = match bytes[6] {
        1 => (usize::from(u16::from_le_bytes([bytes[8], bytes[9]])), 10),
        2 | 3 => {
            if bytes.len() < 12 {
                return Err("truncated NPY v2/v3 header".into());
            }
            (
                u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]) as usize,
                12,
            )
        }
        _ => return Err("unsupported NPY version".into()),
    };
    let payload_start = prefix + header_length;
    if payload_start > bytes.len() {
        return Err("truncated NPY header".into());
    }
    let header =
        std::str::from_utf8(&bytes[prefix..payload_start]).map_err(|error| error.to_string())?;
    let (precision, fortran_order, shape) = parse_header(header)?;
    let count = shape
        .iter()
        .try_fold(1_usize, |left, right| left.checked_mul(*right))
        .ok_or("NPY shape overflows address space")?;
    let width = if precision == Precision::Float32 {
        4
    } else {
        8
    };
    if bytes.len() - payload_start != count * width {
        return Err("NPY payload length disagrees with shape".into());
    }
    let values = bytes[payload_start..]
        .chunks_exact(width)
        .map(|chunk| {
            if width == 4 {
                f64::from(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            } else {
                f64::from_le_bytes([
                    chunk[0], chunk[1], chunk[2], chunk[3], chunk[4], chunk[5], chunk[6], chunk[7],
                ])
            }
        })
        .collect::<Vec<_>>();
    if values.iter().any(|value| !value.is_finite()) {
        return Err("NPY payload contains non-finite values".into());
    }
    Ok(NpyArray {
        precision,
        shape: shape.clone(),
        fortran_order,
        values: if fortran_order {
            fortran_to_c(&values, &shape)
        } else {
            values
        },
    })
}

/// Write little-endian C-order floating-point NPY v1 data.
///
/// # Errors
///
/// Returns an error when shape/payload disagree or filesystem writing fails.
pub fn write_npy<T: FlowScalar>(path: &Path, shape: &[usize], values: &[T]) -> Result<(), String> {
    let count = shape
        .iter()
        .try_fold(1_usize, |left, right| left.checked_mul(*right))
        .ok_or("NPY shape overflows address space")?;
    if count != values.len() {
        return Err("NPY shape and payload length differ".into());
    }
    if values.iter().any(|value| !value.is_finite()) {
        return Err("cannot write non-finite NPY values".into());
    }
    let descriptor = if T::PRECISION == Precision::Float32 {
        "<f4"
    } else {
        "<f8"
    };
    let shape_text = if shape.len() == 1 {
        format!("{},", shape[0])
    } else {
        shape
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(", ")
    };
    let mut header =
        format!("{{'descr': '{descriptor}', 'fortran_order': False, 'shape': ({shape_text}), }}");
    let padding = (16 - ((10 + header.len() + 1) % 16)) % 16;
    header.push_str(&" ".repeat(padding));
    header.push('\n');
    let length = u16::try_from(header.len()).map_err(|_| "NPY v1 header is too long")?;
    let width = if T::PRECISION == Precision::Float32 {
        4
    } else {
        8
    };
    let mut output = Vec::with_capacity(10 + header.len() + width * values.len());
    output.extend_from_slice(b"\x93NUMPY\x01\x00");
    output.extend_from_slice(&length.to_le_bytes());
    output.extend_from_slice(header.as_bytes());
    for value in values {
        if T::PRECISION == Precision::Float32 {
            #[allow(clippy::cast_possible_truncation)]
            output.extend_from_slice(&(value.to_f64() as f32).to_le_bytes());
        } else {
            output.extend_from_slice(&value.to_f64().to_le_bytes());
        }
    }
    fs::write(path, output).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{read_npy, write_npy};

    #[test]
    fn c_order_roundtrip_preserves_f32() {
        let path = std::env::temp_dir().join(format!("foilbench-npy-{}.npy", std::process::id()));
        write_npy(
            &path,
            &[1, 2, 2, 2],
            &[1.0_f32, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        )
        .unwrap();
        let decoded = read_npy(&path).unwrap();
        assert_eq!(decoded.shape, vec![1, 2, 2, 2]);
        assert_eq!(decoded.values, vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]);
        std::fs::remove_file(path).unwrap();
    }
}
