# Android ADB Wireless-Debug Troubleshooting — "Connection reset by peer"

## The symptom that means "stale adb key / version mismatch" (not a code typo)

```
* daemon not running; starting now at tcp:5037
* daemon started successfully
error: protocol fault (couldn't read status): Connection reset by peer
```

Appears on `adb connect`, `adb pair`, AND plain `adb devices -l`. The TLS
handshake is reset by the device **before** the on-screen Allow dialog is
offered — so the user never sees the Allow prompt. This is the signature of a
key/version mismatch between the Termux `adb` client and the tablet's
Wireless-Debugging paired key.

Classic trigger: "it worked consistently last week, now every `adb` command
fails." → the Termux `adb` version (or its key) changed; the tablet still holds
the old paired key.

## Diagnostic steps (run in Termux)

```sh
# 1. Is there more than one adb, and are they different versions?
which -a adb
/data/data/com.termux/files/usr/opt/android-sdk/platform-tools/adb version   # SDK (v36)
/data/data/com.termux/files/usr/bin/adb version                                  # stray (v35)?

# 2. Any orphaned daemon from the old version? (stale log on a DIFFERENT port)
ls -la /data/data/com.termux/files/usr/tmp/adb.*.log
ps -ef | grep '[a]db'            # expect only the client; no lingering daemon

# 3. Current client key is stable? (usually yes — the mismatch is device-side)
stat -c '%y' ~/.android/adbkey
awk '{print $1}' ~/.android/adbkey.pub | head -c 40   # fingerprint offered to tablet

# 4. No competing server port?
echo "ANDROID_ADB_SERVER_PORT=${ANDROID_ADB_SERVER_PORT:-<unset>}"
```

If `which -a adb` shows two binaries with two versions, or a stale
`adb.<otherport>.log` exists, that is the cause.

## Fix (device-side + env cleanup — agent does NOT automate pairing)

1. Kill any orphaned daemon: `adb kill-server`; `pkill -f 'platform-tools/adb'`
   and any `/usr/bin/adb`; confirm `ps -ef | grep [a]db` is empty. Remove the
   stale `adb.<otherport>.log`.
2. Single canonical `adb`: `which adb` → the SDK v36 path; deprioritize/remove
   the stray `/usr/bin/adb` from PATH. `which -a adb` should show ONE.
3. **Tablet: Settings → Developer options → Wireless debugging → OFF, wait 2s,
   ON.** Wipes the device-side stale paired key. Then **Pair device with pairing
   code** again, `adb connect` to the **connect** port, tap **Allow** on the
   tablet. The clean v36 key now registers.
4. Still failing after a toggle? **Developer options → Revoke USB debugging
   authorizations** (clears all paired keys) → re-pair fresh.
5. Only after `adb devices` lists the phone as `device` run `main.py`/`attach.py`.

## Critical rule

"Connection reset by peer" + no Allow prompt = stale key/version → **fix
device-side (toggle/re-pair)**. Do NOT loop retrying the 6-digit pairing code;
that re-pairs a key the device already rejected at the handshake layer. More
code entry does not help — the toggle/re-pair does.

## How this differs from the other pitfall

The "WiFi-Debug port cannot be scripted" reality (separate section) is about
the *port* being non-deterministic. This pitfall is about the *key/handshake* —
even with the correct port known, a key mismatch must be cleared on-device
first. Both are device-side; neither is a forwarder bug.
