# Local Protocol Command Testing Guide

## Context

This project is a Home Assistant integration for Arctic Spas hot tubs. It has two connection modes:
- **Cloud mode**: polls `https://api.myarcticspa.com` (working)
- **Local mode**: persistent TCP connection directly to the spa on port 12121 (implemented, needs field number verification)

The local protocol uses protobuf over a custom framing protocol. All command field numbers were reverse-engineered from the Android APK (`com.levven.protobuf.coldfire.SpaCommand$spa_command`). Two fields are confirmed by live testing; the rest need verification now.

## Hardware

- **Spa board**: YOCTUB firmware 1.0.43 / LPC 1.1.44 / SpaBoy 1.0.32
- **Spa IP**: 192.168.50.70
- **Protocol**: Port 12121, magic `ab ad 1d 3a`, persistent TCP session

## Field numbers to verify

| Command | Field | Confirmed? |
|---------|-------|-----------|
| temperature setpoint | 1 | ✓ YES |
| pump1 | 2 | ✓ YES (cycle trigger) |
| pump2 | 3 | no |
| pump3 | 4 | no |
| blower1 | 7 | no |
| blower2 | 8 | no |
| lights | 9 | ✓ YES |
| filter toggle | 11 | no — was previously field 13 (wrong) |
| easymode / ALL_ON | 17 | no — was previously field 21 (wrong, those tests failed) |
| fogger | 18 | no — was previously field 24 (wrong) |
| boost | 19 | no — was previously field 18 (wrong) |
| sds | 22 | no — was previously field 30 (wrong) |
| yess | 23 | no — was previously field 31 (wrong) |

## Test tool

```
python scripts/control_test.py <host> <command> [value]
```

The script:
1. Connects to the spa and establishes a persistent session
2. Reads the baseline spa state (2 seconds)
3. Sends the command
4. Waits 1.2 seconds, then reads the result state (4 seconds)
5. Prints a diff showing exactly what changed

A **pass** is any field in `spa_live` changing to the expected value.
A **fail** is no fields changing (command silently ignored = wrong field number).

> **Important**: The spa only allows one TCP connection at a time. If HA is running with the integration active, it holds the session. Stop HA or disable the integration before running these tests.

---

## Test sequence

Work through these in order. The spa's current state matters — check the "After state" output before each test.

### 1. Filter toggle (field 11)

The filter should currently be idle or filtering. If it's in boost mode (6), wait for it to finish.

```
python scripts/control_test.py 192.168.50.70 filter on
```

**Pass**: `filter_status` changes to `filter` (2) or `purge` (1)

```
python scripts/control_test.py 192.168.50.70 filter off
```

**Pass**: `filter_status` changes to `idle` (0) or `suspended` (3)

If no change, try the old wrong field numbers to rule out a different issue:
```
python scripts/control_test.py 192.168.50.70 raw 13 1
python scripts/control_test.py 192.168.50.70 raw 12 1
```

---

### 2. Easy mode / ALL_ON (field 17)

This is the "EZ" button in the app. It should turn all jets on at once. The read-back field is `allOn_easymode` (spa_live field 24).

Make sure the spa is in a neutral state (pump1 low or off, lights either state).

```
python scripts/control_test.py 192.168.50.70 easymode on
```

**Pass**: `allOn_easymode` (spa_live field 24) changes to `1`, and pump/jet fields jump to high. Also possibly `stereo` (field 11 in spa_live) goes to 1.

```
python scripts/control_test.py 192.168.50.70 easymode off
```

**Pass**: `allOn_easymode` goes back to `0`.

If field 24 doesn't change, check whether field 22 (`economy`) changed instead — that would indicate a different mapping than expected. Try:
```
python scripts/control_test.py 192.168.50.70 raw 17 1
python scripts/control_test.py 192.168.50.70 raw 18 1
```

---

### 3. Boost (field 19)

Boost runs the filter at high speed for a timed cycle. It can't be stopped once started.

> **Caution**: This will run for a full boost cycle (~20 min). Only run if you don't mind the filter running.

```
python scripts/control_test.py 192.168.50.70 boost
```

**Pass**: `filter_status` changes to `boost` (6). Pump1 will likely go to high and lock there for the duration.

If no change, try:
```
python scripts/control_test.py 192.168.50.70 raw 19 1
python scripts/control_test.py 192.168.50.70 raw 18 1
```

---

### 4. Pump2 (field 3)

Note: pump commands are **cycle triggers** for pump1 (off→low→high→off). For pump2+ it may be a direct set. The expected on-value is 2 (high).

Make sure pump2 is currently off.

```
python scripts/control_test.py 192.168.50.70 pump2 on
```

**Pass**: `pump2` (spa_live field 4) changes from `off` to `hi`.

```
python scripts/control_test.py 192.168.50.70 pump2 off
```

**Pass**: `pump2` goes back to `off`.

If the value 2 doesn't work as "on", try value 1:
```
python scripts/control_test.py 192.168.50.70 raw 3 1
```

---

### 5. Pump3 (field 4)

Same pattern as pump2. Only run if your spa has a pump3 (check the baseline "After state" — if pump3 never appears, your spa may not have one).

```
python scripts/control_test.py 192.168.50.70 pump3 on
python scripts/control_test.py 192.168.50.70 pump3 off
```

---

### 6. Blower1 (field 7)

Only run if your spa has blowers (check baseline — if `blower1` is absent from the after-state, skip).

The value sent is `1` for on (booleans in the coldfire schema), unlike the old wrong value of `2`.

```
python scripts/control_test.py 192.168.50.70 blower1 on
```

**Pass**: `blower1` (spa_live field 8) goes to non-zero.

```
python scripts/control_test.py 192.168.50.70 blower1 off
```

If value `1` doesn't work, try `2`:
```
python scripts/control_test.py 192.168.50.70 raw 7 2
```

---

### 7. Fogger (field 18)

Only run if your spa has a fogger.

```
python scripts/control_test.py 192.168.50.70 fogger on
```

**Pass**: `fogger` (spa_live field 25) changes to `1`.

```
python scripts/control_test.py 192.168.50.70 fogger off
```

---

### 8. SDS (field 22) and YESS (field 23)

Only run if your spa has these features.

```
python scripts/control_test.py 192.168.50.70 sds on
python scripts/control_test.py 192.168.50.70 sds off

python scripts/control_test.py 192.168.50.70 yess on
python scripts/control_test.py 192.168.50.70 yess off
```

**Pass**: `sds` (spa_live field 31) or `yess` (spa_live field 32) toggles.

---

## If a command produces no change

1. Check if the spa was already in the requested state (not a failure — script will note this)
2. Check for state locks printed by the script (filter=boost locks some commands)
3. Try `raw <field> <value>` with adjacent field numbers (+1, -1) to find the real one
4. Report back: paste the full script output including "After state (all fields)"

## Reporting results

For each test, note:
- **Pass / Fail**
- The exact field that changed (from the diff output)
- Any unexpected fields that also changed
- The full "After state" if the result is surprising

These results go into `local_api.py` comments and `CLAUDE.md` to mark each field as confirmed.

---

## File locations

- Test script: `scripts/control_test.py`
- Local API implementation: `custom_components/arctic_spas/local_api.py`
- Project context: `CLAUDE.md`
