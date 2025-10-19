# conftest.py
import os
import pytest
import httpx

BASE = os.environ.get("WAAH_BASE", "http://localhost:8000")

@pytest.fixture(scope="session")
def base_url():
    return BASE.rstrip("/")

@pytest.fixture(scope="session")
def client():
    with httpx.Client(timeout=10) as c:
        yield c

@pytest.fixture(scope="session")
def auth_headers(client, base_url):
    # ensure server is up
    r = client.get(f"{base_url}/healthz")
    assert r.status_code == 200, f"/healthz failed: {r.text}"

    # seed dev data
    r = client.post(f"{base_url}/admin/dev-bootstrap")
    assert r.status_code == 200, f"/admin/dev-bootstrap failed: {r.text}"
    boot = r.json()

    # login (password)
    r = client.post(f"{base_url}/auth/login", params={"mobile":"9999999999","password":"admin"})
    assert r.status_code == 200, f"/auth/login failed: {r.text}"
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}

@pytest.fixture(scope="session")
def rng_suffix():
    import random, string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
