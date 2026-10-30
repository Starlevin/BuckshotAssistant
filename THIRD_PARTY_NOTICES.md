# Third-party components

## bx6xb/buckshot-roulette-solver

Source: https://github.com/bx6xb/buckshot-roulette-solver

Pinned revision: `49f3511b9db7be6237329a722514f9db91756ea5`

License: MIT. Original copyright and license are preserved in
`third_party/buckshot-roulette-solver/LICENSE` and must accompany distributions.

The local adapter uses the project's `ai_engine.py` strategy implementation
and `best.onnx` object detection model. Local window capture, candidate handling,
state validation and sidebar are separate integration code.

Vision detections are candidates requiring review. The upstream model describes
the single-player Double-or-Nothing dealer and can use heuristic fallback;
recommendations must not be represented as guaranteed outcomes or a solver for
every game mode.
