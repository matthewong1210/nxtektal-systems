# Equipment assets (placeholder slot)

Empty by design — no manufacturer drawings yet.

Will hold, per supported washer/hopper model:

* `<manufacturer>_<model>.usd` — inlet + body geometry (from manufacturer
  drawings or field measurement; request list in docs/missing_inputs.md)
* matching `configs/equipment/<manufacturer>_<model>.yaml` profile

The USD only needs docking-relevant fidelity: inlet opening, guarding, and
collision envelope. Photorealism is explicitly out of scope.
