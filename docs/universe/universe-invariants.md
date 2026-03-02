# Universe Invariants

1. Universe membership is immutable once snapshot stored.

2. UniverseSnapshot must be versioned.

3. Every RunManifest must reference:
   - universe_version
   - snapshot_date

4. Universe selection logic must be pure function of:
   - historical market data
   - filter configuration

5. UniverseSnapshot must be reproducible from:
   - dataset version
   - filter hash
   - snapshot date

6. Symbol lifecycle mapping must not alter historical bars.
