# Draft Suite first-owner-use checklist

This is the final human gate for trusting the cockpit during a real draft. Run it first against a
mock or disposable league; record surprises even when the final recommendation was acceptable.

## Session

- Date:
- League/session:
- Device and viewport:
- Manual, Yahoo OAuth shadow, or Yahoo scraper shadow:
- Projection/ranking snapshot source and age:

## Before the clock

- [ ] Team count, round count, snake order, owner slot, roster slots, and scoring are correct.
- [ ] The page clearly states that Open Gridiron will not submit a pick to Yahoo.
- [ ] Search, filters, queue reorder, and keyboard focus are comfortable under time pressure.
- [ ] Three recommendation cards explain value, roster impact, tier urgency, risk, and tradeoff.
- [ ] Unknown next-turn availability is labeled unknown instead of displaying a made-up percentage.
- [ ] Manual entry remains available when Yahoo, news, simulation, and AI enhancements are disabled.

## During the rehearsal

- [ ] Record at least one full round manually without losing board position.
- [ ] Trigger a stale browser conflict and confirm refresh preserves the intended highlighted player.
- [ ] Correct or undo a pick and confirm the board/recommendations advance from canonical history.
- [ ] Pause, reload, resume, and continue without duplicate or missing events.
- [ ] Confirm exact Yahoo observations and review missing/conflicting observations as proposals.
- [ ] Reject at least one proposal and verify that Yahoo does not overwrite the manual board.
- [ ] After validating the feed, explicitly enable auto-approve and confirm that only clean,
      next-in-sequence mapped picks apply automatically.
- [ ] Feed an unknown, conflicting, or out-of-order pick in auto-approve mode and verify that it
      remains a proposal; then select **Require approval** and verify future clean picks also wait.
- [ ] Run one optional scenario; a timeout must leave deterministic advice intact.
- [ ] Verify mobile/tablet layout has no horizontal overflow or hidden primary action.

## After completion

- [ ] Replay shows the recommendation snapshot that existed at each owner turn.
- [ ] Replay does not use later NFL outcomes or current projections to rewrite old advice.
- [ ] Waiver watchlist and coaching notes identify evidence thresholds and avoid weak claims.
- [ ] Diagnostics contain sequence/input/version evidence but no credentials, cookies, or private prose.
- [ ] Export/backup succeeds before the session is treated as durable history.

## Verdict

- [ ] Ready for owner use in manual, Yahoo approval, and owner-confirmed safe auto-approve modes.
- [ ] Blocked; issue and affected sequence recorded below.

Notes:

```text

```

An attended owner may explicitly promote a feed already observed in approval mode to auto-approve
safe sequential picks for that session. Evidence-based promotion without live owner confirmation
retains the separate gate: one persisted rehearsal with at least 50 consecutive correctly normalized
picks and no unresolved identity or ordering failure.
