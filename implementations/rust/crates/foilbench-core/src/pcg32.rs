//! Portable PCG-XSH-RR 64/32 used by every `FoilBench` implementation.

const MULTIPLIER: u64 = 6_364_136_223_846_793_005;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Pcg32 {
    state: u64,
    increment: u64,
}

impl Pcg32 {
    #[must_use]
    pub fn new(seed: u64, stream: u64) -> Self {
        let mut selected = Self {
            state: 0,
            increment: (stream << 1) | 1,
        };
        let _ = selected.next_u32();
        selected.state = selected.state.wrapping_add(seed);
        let _ = selected.next_u32();
        selected
    }

    #[must_use]
    pub const fn checkpoint(self) -> (u64, u64) {
        (self.state, self.increment)
    }

    /// Restore a complete portable generator checkpoint.
    ///
    /// # Errors
    ///
    /// Returns an error when the stream increment is not odd.
    pub fn restore(&mut self, checkpoint: (u64, u64)) -> Result<(), &'static str> {
        if checkpoint.1 & 1 == 0 {
            return Err("PCG32 increment must be odd");
        }
        (self.state, self.increment) = checkpoint;
        Ok(())
    }

    pub fn next_u32(&mut self) -> u32 {
        let old = self.state;
        self.state = old.wrapping_mul(MULTIPLIER).wrapping_add(self.increment);
        #[allow(clippy::cast_possible_truncation)]
        let xorshifted = (((old >> 18) ^ old) >> 27) as u32;
        xorshifted.rotate_right((old >> 59) as u32)
    }

    #[allow(clippy::cast_precision_loss)] // Required by the shared Float32 conversion.
    pub fn next_f32(&mut self) -> f32 {
        self.next_u32() as f32 * (1.0_f32 / 4_294_967_296.0_f32)
    }
}

#[cfg(test)]
mod tests {
    use super::Pcg32;

    #[test]
    fn matches_shared_seed_42_prefix() {
        let mut rng = Pcg32::new(42, 54);
        let expected = [2_707_161_783, 2_068_313_097, 3_122_475_824, 2_211_639_955];
        assert_eq!(expected.map(|_| rng.next_u32()), expected);
    }

    #[test]
    fn checkpoint_restores_exact_stream() {
        let mut rng = Pcg32::new(7, 3);
        let checkpoint = rng.checkpoint();
        let first = rng.next_u32();
        rng.restore(checkpoint).expect("valid checkpoint");
        assert_eq!(rng.next_u32(), first);
    }
}
