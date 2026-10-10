-- 005: TimescaleDB compression for vehicle_positions (the number this migration had reserved).
--
-- Chunks older than 1 day are compressed by Tiger's background policy. Everything the live app reads (the last few
-- hours: matching, where now, the plan) stays in uncompressed 6 h chunks, so live queries are unchanged. Older
-- chunks (replays, backtests) are still read by the same SQL; compression is transparent to queries.
-- segmentby covers the unique key's columns (mode, source, vehicle_id) and orderby its time (ts), as compression
-- requires for a unique index; the recorders' INSERT ... ON CONFLICT DO UPDATE still works if a late replay lands
-- in a compressed chunk (TimescaleDB 2.11 and later; Tiger runs 2.30).

ALTER TABLE vehicle_positions SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'mode, source, vehicle_id',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('vehicle_positions', compress_after => INTERVAL '1 day', if_not_exists => TRUE);
