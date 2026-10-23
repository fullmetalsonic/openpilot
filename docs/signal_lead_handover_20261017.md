# Auxiliary red veto and existing lead handover — 2026-10-17

## FMS policy follow-up — 2026-10-23

The FMS user now explicitly selects lead priority in normal active ACC even
with red/unknown observations; its earlier deferral of this upstream policy is
superseded. Require current real enabled/longActive control, drive/valid inputs,
no driver cancel/negative request, pedal/override, independent SoftHold, parking
hold or explicit stop. Non-PCM OEM CC.cancel is not driver CANCEL. Missing proof
is not permission. Only effective auxiliary decisions and the FMS added
owned-stay force yield; raw recognition, guard red memory, real navigation stay
and native FSM survive. Qualifying lead loss restores retained red next cycle.
No radar-only, persistent identity episode or motion delay is introduced.

This is an intentional policy choice, not proof of ego-lane signal assignment.
The previously recorded transient vision lead opening the exception for about
449ms and roughly1.53m movement under red remains a known limitation. The
relative model-distance boundary may toggle. Neither is claimed fixed here.
Local source/host checks do not establish native solver, IPC, ECU or road
behavior. No commit, push, device or Params change is authorized by this note.
Local checks: 1,233 affected host cases plus four separate native/auxiliary
acquisition-order and same-cycle lead-loss/OFF cases pass. The actual saved
settings reader/runtime/MPC obstacle and final-veto paths are included; native
optimization and physical restart remain unrun. The initial 62-case lead suite
is a subset of the1,233, not an additional independent count.
The original upstream implementation/validation history follows below.

The user requested that a lead satisfying the existing stop-to-lead range
condition take priority over the auxiliary red departure veto. Previously the
veto ran before the existing handover and forced e2eStopped even when the model
wanted to follow a departing lead.

The shared condition is leadOne.status, a finite positive dRel, finite filtered
model stop distance, and `dRel - stop_model_x_raw < 2.0`. The model distance is
the existing `update_stop_dist(x[31])` result, before its closing-rate limiter.
Exactly 2m is excluded. This is not an absolute 2m following distance, nor an
independent lead-motion detector. A stationary qualifying lead remains an MPC
obstacle; enabling lead following does not command acceleration through it.

When eligible, the effective start decision has no hold, refusal warning,
green confirmation or green-release event. The final longitudinal veto sees
this same decision. The optional legacy SignalAssist decision also suppresses
its hold and red injection. Raw recognition remains available to the display;
the underlying red memory is retained. Losing the lead or leaving the boundary
reapplies any retained veto on the next planner update, including with unknown
camera evidence. There is no persistent bypass timer. Suppressed warnings do
not consume the warning needed when the veto resumes.

Soft hold and explicit user stop remain independent. Existing radar/MPC
obstacles, pedal handling, mode-0 shutdown, mode-2 moving preparation, detector
and green-confirmation policies are unchanged. Normal e2eStopped handover still
has its existing one-cycle zero cruise target before the lead state update.

Validation: 715 desktop signal, planner, geometry, timing and brake-display
tests pass (316 pre-existing NumPy deprecation warnings). The new 22 cases
exercise both ACC/blended paths, strict range boundaries, invalid distances,
both auxiliary vetoes, the real final-output veto method, continuous lead
following, lead disappearance/range exit, unknown evidence, refusal warning,
raw red display and independent driver holds. Existing tests requiring red
priority now explicitly place the lead outside the handover boundary. Tests
use the repository's production-method replay harness and a native Params
lookup double on Windows. They are not closed-loop MPC or road validation.

Publish as single-parent commits to carrot-nav and carrot-wip. No vehicle
installation, fresh log replay, model retraining or recognition improvement is
claimed by this change.
