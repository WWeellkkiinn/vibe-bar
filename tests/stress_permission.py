"""Stress-test for VibeBar PermissionServer.
Runs without UI — hits server via HTTP, validates response shape, edge cases.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from permission_server import PermissionServer  # noqa: E402


def post(url: str, body: dict, timeout: float = 30.0) -> tuple[int, dict | None]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None


def post_raw(host: str, port: int, path: str, body_bytes: bytes,
             content_length: int | None = None) -> tuple[int, bytes]:
    """Low-level raw POST for malformed-body tests."""
    cl = content_length if content_length is not None else len(body_bytes)
    s = socket.create_connection((host, port), timeout=10)
    s.sendall(
        f"POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {cl}\r\n\r\n".encode()
        + body_bytes
    )
    resp = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    s.close()
    head, _, body = resp.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    status = int(status_line.split()[1])
    return status, body


def expect(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        global FAILED
        FAILED += 1


FAILED = 0


def main() -> int:
    srv = PermissionServer(preferred_port=29847, port_range=5)
    port = srv.start()
    base = f"http://127.0.0.1:{port}"
    print(f"Stress test starting on {base}\n")

    # ── 1. Bash permission: basic allow ──────────────────────────────────────
    print("[1] Bash permission allow")
    result_holder: dict = {}
    def req1():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-1",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_use_id": "tu-1",
        })
    t = threading.Thread(target=req1); t.start()
    time.sleep(0.2)
    pending = srv.list_pending()
    expect(len(pending) == 1, "1 pending after POST")
    expect(pending[0]["kind"] == "permission", "kind=permission")
    expect(pending[0]["tool_input_brief"].get("command") == "echo hello", "command extracted")
    srv.resolve(pending[0]["id"], {"behavior": "allow"})
    t.join(timeout=5)
    status, body = result_holder["r"]
    expect(status == 200, "200 OK")
    expect(body["hookSpecificOutput"]["decision"]["behavior"] == "allow", "allow returned")
    expect(len(srv.list_pending()) == 0, "pending cleared after resolve")

    # ── 2. AskUserQuestion routed via /permission, kind→elicitation ──────────
    print("\n[2] AskUserQuestion via /permission")
    def req2():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-2",
            "tool_name": "AskUserQuestion",
            "tool_input": {
                "questions": [{
                    "question": "Which lang?",
                    "header": "Language",
                    "multiSelect": False,
                    "options": [
                        {"label": "Python", "description": "Easy"},
                        {"label": "Rust", "description": "Fast"},
                    ],
                }],
            },
        })
    t = threading.Thread(target=req2); t.start()
    time.sleep(0.2)
    pending = srv.list_pending()
    expect(pending[0]["kind"] == "elicitation", "kind=elicitation")
    expect(len(pending[0]["suggestions"]) == 2, "2 suggestions extracted")
    expect(pending[0]["suggestions"][0]["question"] == "Which lang?", "question text propagated")
    expect(pending[0]["suggestions"][0]["description"] == "Easy", "description propagated")
    srv.resolve(pending[0]["id"], {
        "behavior": "allow",
        "updatedInput": {
            "questions": pending[0]["tool_input_brief"]["questions"],
            "answers": {"Which lang?": "Python"},
        }
    })
    t.join(timeout=5)
    status, body = result_holder["r"]
    expect(body["hookSpecificOutput"]["hookEventName"] == "PermissionRequest", "envelope event = PermissionRequest")
    expect(body["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]["Which lang?"] == "Python",
           "answer routed back")

    # ── 3. ExitPlanMode → elicitation ────────────────────────────────────────
    print("\n[3] ExitPlanMode via /permission")
    def req3():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-3",
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "do X then Y"},
        })
    t = threading.Thread(target=req3); t.start()
    time.sleep(0.2)
    pending = srv.list_pending()
    expect(pending[0]["kind"] == "elicitation", "ExitPlanMode kind=elicitation")
    srv.resolve(pending[0]["id"], {"behavior": "allow"})
    t.join(timeout=5)

    # ── 4. Notification: must NOT register pending ───────────────────────────
    print("\n[4] Notification returns 204 immediately, no pending")
    status, body = post(f"{base}/notification", {
        "session_id": "stress-4",
        "notification_type": "permission_prompt",
        "message": "hey",
    })
    expect(status == 204, "204 returned")
    time.sleep(0.2)
    expect(len(srv.list_pending()) == 0, "no pending registered")

    # Also test notification with NO type (the unknown-type bug user hit)
    status, body = post(f"{base}/notification", {"session_id": "stress-4b", "message": "x"})
    expect(status == 204, "unknown notif type: 204 still")
    expect(len(srv.list_pending()) == 0, "unknown notif: no pending")

    # ── 5. Deny path ─────────────────────────────────────────────────────────
    print("\n[5] Deny path")
    def req5():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-5", "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"}, "tool_use_id": "tu-5",
        })
    t = threading.Thread(target=req5); t.start()
    time.sleep(0.2)
    pid = srv.list_pending()[0]["id"]
    srv.resolve(pid, {"behavior": "deny", "message": "absolutely not"})
    t.join(timeout=5)
    _, body = result_holder["r"]
    expect(body["hookSpecificOutput"]["decision"]["behavior"] == "deny", "deny returned")
    expect(body["hookSpecificOutput"]["decision"].get("message") == "absolutely not", "deny msg returned")

    # ── 6. Concurrent: 20 parallel permission requests ───────────────────────
    print("\n[6] 20 concurrent permission requests, resolve all")
    results = [None] * 20
    def req6(i: int):
        results[i] = post(f"{base}/permission", {
            "session_id": f"sess-{i % 4}",   # 4 distinct sessions
            "tool_name": "Bash",
            "tool_input": {"command": f"echo job-{i}"},
            "tool_use_id": f"tu-c-{i}",
        })
    pool = ThreadPoolExecutor(max_workers=20)
    futures = [pool.submit(req6, i) for i in range(20)]
    time.sleep(0.5)
    pending = srv.list_pending()
    expect(len(pending) == 20, f"20 pending registered (got {len(pending)})")
    # Resolve all
    for p in pending:
        srv.resolve(p["id"], {"behavior": "allow"})
    for f in as_completed(futures, timeout=10):
        f.result()
    pool.shutdown(wait=True)
    expect(all(r[0] == 200 for r in results), "all 20 returned 200")
    expect(all(r[1]["hookSpecificOutput"]["decision"]["behavior"] == "allow" for r in results),
           "all 20 got allow")
    expect(len(srv.list_pending()) == 0, "no leaks after concurrent resolve")

    # ── 7. Long Bash command truncation ──────────────────────────────────────
    print("\n[7] Long Bash command truncation")
    long_cmd = "echo " + "A" * 5000
    def req7():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-7", "tool_name": "Bash",
            "tool_input": {"command": long_cmd}, "tool_use_id": "tu-7",
        })
    t = threading.Thread(target=req7); t.start()
    time.sleep(0.2)
    brief = srv.list_pending()[0]["tool_input_brief"]
    expect(len(brief["command"]) <= 1000, f"command truncated ({len(brief['command'])} chars)")
    srv.resolve(srv.list_pending()[0]["id"], {"behavior": "allow"})
    t.join(timeout=5)

    # ── 8. AskUserQuestion with 10 options ───────────────────────────────────
    print("\n[8] AskUserQuestion with 10 options")
    def req8():
        result_holder["r"] = post(f"{base}/permission", {
            "session_id": "stress-8",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{
                "question": "Pick", "header": "P",
                "options": [{"label": f"opt-{i}", "description": f"d-{i}"} for i in range(10)],
            }]},
        })
    t = threading.Thread(target=req8); t.start()
    time.sleep(0.2)
    pending = srv.list_pending()[0]
    expect(len(pending["suggestions"]) == 10, f"10 suggestions kept ({len(pending['suggestions'])})")
    srv.resolve(pending["id"], {"behavior": "allow"})
    t.join(timeout=5)

    # ── 9. Malformed body: invalid JSON ──────────────────────────────────────
    print("\n[9] Malformed JSON body")
    status, _ = post_raw("127.0.0.1", port, "/permission", b"{not json")
    expect(status == 400, f"malformed JSON → 400 (got {status})")

    # ── 10. Empty body: still blocks; UI side must resolve ───────────────────
    print("\n[10] Empty body accepted (blocks until resolve)")
    def req10():
        result_holder["r"] = post(f"{base}/permission", {})
    t = threading.Thread(target=req10); t.start()
    time.sleep(0.2)
    pending = srv.list_pending()
    expect(len(pending) == 1, "empty-body request blocks pending")
    srv.resolve(pending[0]["id"], {"behavior": "deny"})
    t.join(timeout=5)
    expect(result_holder["r"][0] == 200, "empty body accepted, returns 200")

    # ── 11. Unknown route ────────────────────────────────────────────────────
    print("\n[11] Unknown route → 404")
    status, _ = post_raw("127.0.0.1", port, "/nope", b"{}")
    expect(status == 404, f"unknown path → 404 (got {status})")

    # ── 12. GET method → 405 ─────────────────────────────────────────────────
    print("\n[12] GET on /permission → 405")
    try:
        urllib.request.urlopen(f"{base}/permission", timeout=3)
        expect(False, "GET should not succeed")
    except urllib.error.HTTPError as e:
        expect(e.code == 405, f"GET → 405 (got {e.code})")

    # ── 13. resolve() on unknown id ──────────────────────────────────────────
    print("\n[13] resolve() unknown id")
    expect(srv.resolve("nonexistent", {"behavior": "allow"}) is False, "unknown id → False")

    # ── 14. Server stop() while pending: should release threads ──────────────
    print("\n[14] stop() while 5 pending should unblock all in <2s")
    blocked_results = [None] * 5
    def req14(i: int):
        blocked_results[i] = post(f"{base}/permission", {
            "session_id": f"stop-{i}", "tool_name": "Bash",
            "tool_input": {"command": "long wait"}, "tool_use_id": f"stop-{i}",
        }, timeout=10)
    pool = ThreadPoolExecutor(max_workers=5)
    futs = [pool.submit(req14, i) for i in range(5)]
    time.sleep(0.5)
    expect(len(srv.list_pending()) == 5, "5 pending registered before stop")
    t0 = time.time()
    srv.stop()
    for f in as_completed(futs, timeout=5):
        f.result()
    elapsed = time.time() - t0
    pool.shutdown(wait=True)
    expect(elapsed < 3.0, f"all 5 threads unblocked in {elapsed:.2f}s")
    # Empty decision means body is "{}"
    expect(all(r[0] == 200 and r[1] == {} for r in blocked_results),
           "all 5 got 200 with empty body (stop semantics)")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    if FAILED == 0:
        print("ALL TESTS PASSED")
        return 0
    print(f"{FAILED} TESTS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
