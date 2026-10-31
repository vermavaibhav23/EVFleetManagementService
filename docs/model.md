# Interview optimization model

Customers have a fixed order and fixed arrival deadlines. Each gap can contain zero or one charging stop from the nearest four compatible available stations. Continuous variables represent charging energy and time; binary variables select stations, ports and tariff/charging segments. SciPy MILP delegates the search to HiGHS.

The objective minimizes whole-journey metered charging cost, then return completion time. Battery energy must remain at or above the normal reserve on every driving arrival, including depot return. A vehicle already stationary at a compatible charger may charge in place from below reserve. Driving reserve exceptions and late-delivery recovery are disabled. Infeasibility means no route was found within the bounded model, not that all possible real-world routes are impossible.

Charging efficiency separates energy stored from energy billed. Piecewise charging power covers 0–80%, 80–90%, and 90–100% battery bands. Tariff integration accounts for changes during a session. Charger occupation includes connection and release, and full rated power is conservatively reserved against the site limit.

The independent validator reconstructs travel, charging duration, cost, battery and booking conflicts before publication and again at approval. Approval commits all bookings atomically in PostgreSQL using a run revision check. Stale plans book nothing. Original deadlines never move.

Fleet planning solves constrained vehicles first and reserves provisional slots before the next solve. This is sequential coordination, not globally optimal fleet optimization. Solver statuses distinguish proven model optimum, feasible incumbent, bounded infeasibility, timeout and validation rejection.

The current implementation retains some internal legacy solver parameters for arithmetic regression coverage; the public demo rejects reserve/deadline exceptions and its seeded policy permits only one charging stop per gap. No emergency recovery mode is exposed.

Numeric tolerances: 0.0002 kWh energy, ₹0.01 cost and approximately 0.1 seconds reconstructed timings. They are numerical tolerances, not extra usable battery. Telemetry event timestamps are stored in PostgreSQL. Kafka offsets commit after transactional state/history work; duplicate IDs and stale sequences cannot undo newer state.
