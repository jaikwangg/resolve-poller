"""test helper: private token map + read-users files in a temp dir, env pointed at them.

Import BEFORE dashboard_api (it only reads the paths per request, but keeping the order makes
the env obvious). Secrets here are test values only.
"""
import atexit, base64, json, os, pathlib, shutil, tempfile

import auth

READ_USER, READ_PASSWORD = "director", "test-read-password-0123456789"
READ_TOKEN = "test-read-token-0123456789abcdef"


def machine_token(host):
    return "test-machine-token-%s-0123456789" % host


def install(hosts=("conform-01", "e2e", "grade-01"), tmp=None):
    if tmp is None:
        tmp = tempfile.mkdtemp(prefix="et-auth-")
        atexit.register(shutil.rmtree, tmp, True)
    tmp = pathlib.Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    tm, ru = tmp / "tokens.json", tmp / "readers.json"
    tm.write_text(json.dumps({"hosts": {h: auth.digest(machine_token(h)) for h in hosts}}))
    ru.write_text(json.dumps({"users": {READ_USER: auth.digest(READ_PASSWORD)},
                              "tokens": {"dash": auth.digest(READ_TOKEN)}}))
    os.chmod(tm, 0o600)
    os.chmod(ru, 0o600)
    os.environ["EDITORTRACK_TOKEN_MAP"] = str(tm)
    os.environ["EDITORTRACK_READ_USERS"] = str(ru)
    os.environ.pop("EDITORTRACK_READ_AUTH", None)
    os.environ.pop("EDITORTRACK_TOKEN", None)
    return tm, ru


def machine(host):
    return {"Authorization": "Bearer " + machine_token(host)}


def basic(user=READ_USER, pw=READ_PASSWORD):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()}


def reader():
    return {"Authorization": "Bearer " + READ_TOKEN}
