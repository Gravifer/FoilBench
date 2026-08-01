const PCG32_MULTIPLIER = UInt64(6364136223846793005)

mutable struct PCG32
    state::UInt64
    increment::UInt64

    function PCG32(seed::Integer, stream::Integer = 54)
        seed >= 0 || throw(ArgumentError("seed must be non-negative"))
        stream >= 0 || throw(ArgumentError("stream must be non-negative"))
        rng = new(UInt64(0), (UInt64(stream) << 1) | UInt64(1))
        next_uint32!(rng)
        rng.state += UInt64(seed)
        next_uint32!(rng)
        return rng
    end
end

function next_uint32!(rng::PCG32)::UInt32
    old = rng.state
    rng.state = old * PCG32_MULTIPLIER + rng.increment
    xorshifted = UInt32((((old >> 18) ⊻ old) >> 27) & UInt64(0xffffffff))
    rotation = Int(old >> 59)
    return (xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))
end

function next_float32!(rng::PCG32)::Float32
    return Float32(next_uint32!(rng)) * Float32(1.0 / 4294967296.0)
end
