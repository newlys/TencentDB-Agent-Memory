from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import warnings
from collections.abc import AsyncGenerator, Callable
from typing import Annotated


WORKSPACE = Path(os.environ.get("BENCHMARK_WORKSPACE", "/workspace"))
if not (WORKSPACE / "fastapi").exists():
    cwd = Path.cwd()
    if (cwd / "fastapi").exists():
        WORKSPACE = cwd

sys.path.insert(0, str(WORKSPACE))

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile  # noqa: E402
from fastapi.exceptions import FastAPIDeprecationWarning  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.datastructures import UploadFile as StarletteUploadFile  # noqa: E402


STATES = {"primary", "edge", "exception", "regression"}


def _pass(state: str) -> None:
    print(json.dumps({"state": state, "passed": True}))


def _assert_state(state: str) -> None:
    if state not in STATES:
        raise AssertionError(f"Unknown grader state: {state}")


def multivalue_shape(state: str) -> None:
    _assert_state(state)
    app = FastAPI()

    @app.get("/list")
    def list_values(tags: list[str] = Query(...)):
        return {"tags": tags}

    @app.get("/tuple")
    def tuple_values(coords: tuple[int, int] = Query(...)):
        return {"coords": coords}

    @app.get("/scalar")
    def scalar_value(mode: str = Query(...)):
        return {"mode": mode}

    client = TestClient(app)

    if state == "primary":
        response = client.get("/list?tags=red&tags=blue&tags=red")
        assert response.status_code == 200, response.text
        assert response.json() == {"tags": ["red", "blue", "red"]}, response.json()

    elif state == "edge":
        response = client.get("/tuple?coords=3&coords=4")
        assert response.status_code == 200, response.text
        assert response.json() == {"coords": [3, 4]}, response.json()

    elif state == "exception":
        response = client.get("/list")
        assert response.status_code == 422, response.text
        details = response.json().get("detail", [])
        assert details, response.json()
        assert any(item.get("loc", [])[-1:] == ["tags"] for item in details), details

    else:  # regression
        response = client.get("/scalar?mode=first&mode=last")
        assert response.status_code == 200, response.text
        assert response.json() == {"mode": "last"}, response.json()

    _pass(state)


def header_multivalue_shape(state: str) -> None:
    _assert_state(state)
    app = FastAPI()

    @app.get("/list")
    def list_values(tags: Annotated[list[str], Header(alias="X-Tag")]):
        return {"tags": tags}

    @app.get("/tuple")
    def tuple_values(pair: Annotated[tuple[str, str], Header(alias="X-Pair")]):
        return {"pair": pair}

    @app.get("/scalar")
    def scalar_value(tag: Annotated[str, Header(alias="X-Tag")]):
        return {"tag": tag}

    @app.get("/optional")
    def optional_values(
        tags: Annotated[list[str] | None, Header(alias="X-Tag")] = None,
    ):
        return {"tags": tags}

    @app.get("/default")
    def default_values(
        tags: Annotated[list[str], Header(alias="X-Tag")] = ["fallback"],
    ):
        return {"tags": tags}

    client = TestClient(app)

    if state == "primary":
        response = client.get(
            "/list", headers=[("X-Tag", "red"), ("X-Tag", "blue")]
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"tags": ["red", "blue"]}, response.json()

    elif state == "edge":
        tuple_response = client.get(
            "/tuple", headers=[("X-Pair", "left"), ("X-Pair", "right")]
        )
        assert tuple_response.status_code == 200, tuple_response.text
        assert tuple_response.json() == {"pair": ["left", "right"]}

        alias_response = client.get(
            "/list", headers=[("X-Tag", "one"), ("X-Tag", "two")]
        )
        assert alias_response.status_code == 200, alias_response.text
        assert alias_response.json() == {"tags": ["one", "two"]}

    elif state == "exception":
        response = client.get(
            "/scalar", headers=[("X-Tag", "red"), ("X-Tag", "blue")]
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"tag": "red"}, response.json()

    else:  # regression
        missing = client.get("/list")
        assert missing.status_code == 422, missing.text
        assert any(
            item.get("loc", [])[-1:] == ["X-Tag"]
            for item in missing.json().get("detail", [])
        ), missing.json()

        optional = client.get("/optional")
        assert optional.status_code == 200, optional.text
        assert optional.json() == {"tags": None}

        defaulted = client.get("/default")
        assert defaulted.status_code == 200, defaulted.text
        assert defaulted.json() == {"tags": ["fallback"]}

        single = client.get("/list", headers={"X-Tag": "solo"})
        assert single.status_code == 200, single.text
        assert single.json() == {"tags": ["solo"]}

    _pass(state)


def _run_upload_case(kind: str) -> tuple[int, list[str], str]:
    closed: list[str] = []
    original_close = StarletteUploadFile.close

    async def tracked_close(self: StarletteUploadFile) -> None:
        closed.append(self.filename or "<unnamed>")
        await original_close(self)

    StarletteUploadFile.close = tracked_close  # type: ignore[method-assign]
    try:
        app = FastAPI()

        if kind == "endpoint-error":
            @app.post("/upload")
            async def upload(file: UploadFile = File(...)):
                raise HTTPException(status_code=409, detail="endpoint boom")

            client = TestClient(app)
            response = client.post(
                "/upload", files={"file": ("boom.txt", b"abc", "text/plain")}
            )

        elif kind == "validation-error":
            @app.post("/upload")
            async def upload(file: UploadFile = File(...), count: int = Form(...)):
                return {"filename": file.filename, "count": count}

            client = TestClient(app)
            response = client.post(
                "/upload",
                data={"count": "not-an-int"},
                files={"file": ("bad.txt", b"abc", "text/plain")},
            )

        else:
            @app.post("/upload")
            async def upload(file: UploadFile = File(...)):
                return {"filename": file.filename}

            client = TestClient(app)
            response = client.post(
                "/upload", files={"file": ("ok.txt", b"abc", "text/plain")}
            )

        return response.status_code, closed, response.text
    finally:
        StarletteUploadFile.close = original_close  # type: ignore[method-assign]


def multipart_cleanup(state: str) -> None:
    _assert_state(state)

    if state == "primary":
        status, closed, text = _run_upload_case("endpoint-error")
        assert status == 409, text
        assert "boom.txt" in closed, closed

    elif state == "edge":
        status, closed, text = _run_upload_case("validation-error")
        assert status == 422, text
        assert "bad.txt" in closed, closed

    elif state == "exception":
        status, _, text = _run_upload_case("endpoint-error")
        assert status == 409, text
        assert "endpoint boom" in text, text

    else:  # regression
        status, closed, text = _run_upload_case("success")
        assert status == 200, text
        assert "ok.txt" in closed, closed

    _pass(state)


class _ExplicitAliasHeaders(BaseModel):
    value: str = Field(alias="X_Custom")


class _ImplicitAliasHeaders(BaseModel):
    user_agent: str


class _RawAliasHeaders(BaseModel):
    x_token: str


def header_alias_exactness(state: str) -> None:
    _assert_state(state)
    app = FastAPI()

    @app.get("/explicit")
    def explicit(data: Annotated[_ExplicitAliasHeaders, Header()]):
        return {"value": data.value}

    @app.get("/implicit")
    def implicit(data: Annotated[_ImplicitAliasHeaders, Header()]):
        return {"value": data.user_agent}

    @app.get("/raw")
    def raw(
        data: Annotated[
            _RawAliasHeaders, Header(convert_underscores=False)
        ],
    ):
        return {"value": data.x_token}

    client = TestClient(app)

    if state == "primary":
        response = client.get("/explicit", headers={"X_Custom": "exact"})
        assert response.status_code == 200, response.text
        assert response.json() == {"value": "exact"}

    elif state == "edge":
        response = client.get("/implicit", headers={"user-agent": "converted"})
        assert response.status_code == 200, response.text
        assert response.json() == {"value": "converted"}

    elif state == "exception":
        response = client.get("/explicit", headers={"X-Custom": "normalized-only"})
        assert response.status_code == 422, response.text

    else:  # regression
        good = client.get("/raw", headers={"x_token": "raw"})
        assert good.status_code == 200, good.text
        assert good.json() == {"value": "raw"}
        bad = client.get("/raw", headers={"x-token": "hyphen"})
        assert bad.status_code == 422, bad.text

    _pass(state)


def _yield_dep_factory(events: list[str], counter: dict[str, int]):
    async def dep() -> AsyncGenerator[int, None]:
        counter["value"] += 1
        value = counter["value"]
        events.append(f"enter-{value}")
        try:
            yield value
        finally:
            events.append(f"exit-{value}")

    return dep


def dependency_scope_cache(state: str) -> None:
    _assert_state(state)

    if state == "primary":
        events: list[str] = []
        counter = {"value": 0}
        dep = _yield_dep_factory(events, counter)
        app = FastAPI()

        @app.get("/")
        async def endpoint(
            function_value: int = Depends(dep, scope="function"),
            request_value: int = Depends(dep, scope="request"),
        ):
            return {"function": function_value, "request": request_value}

        response = TestClient(app).get("/")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["function"] != payload["request"], payload
        assert len([e for e in events if e.startswith("enter-")]) == 2, events
        assert len([e for e in events if e.startswith("exit-")]) == 2, events

    elif state == "edge":
        from fastapi.dependencies.models import Dependant, _get_cache_key

        async def dep() -> AsyncGenerator[None, None]:
            yield None

        function_dep = Dependant(call=dep, scope="function")
        request_dep = Dependant(call=dep, scope="request")
        assert _get_cache_key(dependant=function_dep) != _get_cache_key(
            dependant=request_dep
        )
        assert _get_cache_key(dependant=function_dep)[2] == "function"
        assert _get_cache_key(dependant=request_dep)[2] == "request"

    elif state == "exception":
        events: list[str] = []
        counter = {"value": 0}
        dep = _yield_dep_factory(events, counter)
        app = FastAPI()

        @app.get("/")
        async def endpoint(
            first: int = Depends(dep, scope="request"),
            second: int = Depends(dep, scope="request"),
        ):
            return {"first": first, "second": second}

        response = TestClient(app).get("/")
        assert response.status_code == 200, response.text
        assert response.json()["first"] == response.json()["second"]
        assert len([e for e in events if e.startswith("enter-")]) == 1, events
        assert len([e for e in events if e.startswith("exit-")]) == 1, events

    else:  # regression
        from fastapi.dependencies.models import Dependant, _get_cache_key, _get_computed_scope

        async def dep() -> AsyncGenerator[None, None]:
            yield None

        dependant = Dependant(call=dep)
        assert _get_computed_scope(dependant=dependant) == "request"
        assert _get_cache_key(dependant=dependant)[2] == "request"

    _pass(state)


def response_metadata_consistency(state: str) -> None:
    _assert_state(state)

    if state == "primary":
        app = FastAPI()

        def dep(response: Response) -> None:
            response.headers["x-dependency"] = "present"

        @app.get("/", dependencies=[Depends(dep)])
        def endpoint():
            return {"message": "hello"}

        response = TestClient(app).get("/")
        lengths = response.headers.get_list("content-length")
        assert response.status_code == 200
        assert response.headers["x-dependency"] == "present"
        assert lengths == [str(len(response.content))], lengths

    elif state == "edge":
        app = FastAPI()

        def dep(response: Response) -> None:
            response.status_code = 201
            response.headers["x-dependency"] = "kept"

        @app.get("/", dependencies=[Depends(dep)])
        def endpoint():
            return {"created": True}

        response = TestClient(app).get("/")
        assert response.status_code == 201, response.text
        assert response.headers["x-dependency"] == "kept"
        assert response.headers.get_list("content-length") == [
            str(len(response.content))
        ]

    elif state == "exception":
        app = FastAPI()

        def dep(response: Response) -> None:
            response.headers["x-dependency"] = "kept"

        @app.get("/", status_code=204, dependencies=[Depends(dep)])
        def endpoint():
            return None

        response = TestClient(app).get("/")
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["x-dependency"] == "kept"
        assert response.headers.get_list("content-length") == [], response.headers

    else:  # regression
        app = FastAPI()

        @app.get("/")
        def endpoint():
            return {"plain": True}

        response = TestClient(app).get("/")
        assert response.status_code == 200
        assert response.headers.get_list("content-length") == [
            str(len(response.content))
        ]

    _pass(state)


def deprecation_warning_text(state: str) -> None:
    _assert_state(state)

    if state in {"primary", "edge"}:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            Query(regex="^a+$")

        messages = [str(item.message) for item in seen]
        if state == "primary":
            assert any("use `pattern` instead" in msg for msg in messages), messages
            assert not any("use `example` instead" in msg for msg in messages), messages
        else:
            assert any(
                issubclass(item.category, FastAPIDeprecationWarning) for item in seen
            ), seen

    elif state == "exception":
        app = FastAPI()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastAPIDeprecationWarning)

            @app.get("/")
            def endpoint(q: str = Query(..., regex="^a+$")):
                return {"q": q}

        client = TestClient(app)
        assert client.get("/?q=aaa").status_code == 200
        assert client.get("/?q=bbb").status_code == 422

    else:  # regression
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            Query(pattern="^a+$")
        assert not any(
            issubclass(item.category, FastAPIDeprecationWarning) for item in seen
        ), seen

    _pass(state)


GRADERS: dict[str, Callable[[str], None]] = {
    "FA-SK02-T01": multivalue_shape,
    "FA-SK02-T02": header_multivalue_shape,
    "FA-SK05-T01": multipart_cleanup,
    "FA-SK06-T01": header_alias_exactness,
    "FA-SK07-T01": dependency_scope_cache,
    "FA-SK10-T01": response_metadata_consistency,
    "FA-NS-01": deprecation_warning_text,
}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: grader.py TASK_ID STATE")

    task_id, state = sys.argv[1], sys.argv[2]
    try:
        grader = GRADERS[task_id]
    except KeyError as exc:
        raise SystemExit(f"unknown task: {task_id}") from exc

    grader(state)


if __name__ == "__main__":
    main()
