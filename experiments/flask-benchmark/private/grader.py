from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import warnings
from typing import Callable


WORKSPACE = Path(os.environ.get("BENCHMARK_WORKSPACE", "/workspace"))
if not (WORKSPACE / "src" / "flask").exists():
    cwd = Path.cwd()
    if (cwd / "src" / "flask").exists():
        WORKSPACE = cwd

sys.path.insert(0, str(WORKSPACE / "src"))

from flask import Blueprint  # noqa: E402
from flask import Flask  # noqa: E402
from flask import Request  # noqa: E402
from flask import abort  # noqa: E402
from flask import has_app_context  # noqa: E402
from flask import render_template_string  # noqa: E402
from flask import request  # noqa: E402
import flask.ctx as flask_ctx  # noqa: E402


STATES = {"primary", "edge", "exception", "regression"}


def _pass(state: str) -> None:
    print(json.dumps({"state": state, "passed": True}))


def _assert_state(state: str) -> None:
    if state not in STATES:
        raise AssertionError(f"Unknown grader state: {state}")


def template_precedence(state: str) -> None:
    _assert_state(state)

    if state == "primary":
        app = Flask(__name__)

        @app.context_processor
        def app_values():
            return {"name": "processor", "injected": "yes"}

        with app.test_request_context("/"):
            rendered = render_template_string("{{ name }}|{{ injected }}", name="user")

        assert rendered == "user|yes", rendered

    elif state == "edge":
        app = Flask(__name__)
        bp = Blueprint("bp", __name__)

        @app.context_processor
        def app_values():
            return {"name": "app"}

        @bp.context_processor
        def bp_values():
            return {"name": "blueprint", "bp_only": "present"}

        @bp.get("/")
        def index():
            return render_template_string("{{ name }}|{{ bp_only }}", name="explicit")

        app.register_blueprint(bp, url_prefix="/bp")
        response = app.test_client().get("/bp/")
        assert response.status_code == 200
        assert response.get_data(as_text=True) == "explicit|present"

    elif state == "exception":
        app = Flask(__name__)

        @app.context_processor
        def app_values():
            return {"injected": "processor-only"}

        with app.test_request_context("/"):
            rendered = render_template_string(
                "{{ supplied }}|{{ injected }}", supplied="explicit-only"
            )

        assert rendered == "explicit-only|processor-only"

    else:  # regression
        app = Flask(__name__)
        with app.test_request_context("/"):
            rendered = render_template_string("{{ name }}", name="plain")
        assert rendered == "plain"

    _pass(state)


def request_limit_precedence(state: str) -> None:
    _assert_state(state)
    app = Flask(__name__)
    app.config["MAX_FORM_PARTS"] = 1000
    app.config["MAX_FORM_MEMORY_SIZE"] = 500_000

    with app.test_request_context("/"):
        if state == "primary":
            request.max_form_parts = 17
            assert request.max_form_parts == 17
        elif state == "edge":
            request.max_form_parts = 0
            assert request.max_form_parts == 0
        elif state == "exception":
            request.max_form_parts = None
            assert request.max_form_parts == 1000
        else:  # regression
            request.max_form_memory_size = 123
            assert request.max_form_memory_size == 123
            assert request.max_content_length is None

    _pass(state)


def _flatten_exception_messages(exc: BaseException) -> list[str]:
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [str(exc)]
    out: list[str] = []
    for item in nested:
        out.extend(_flatten_exception_messages(item))
    return out


def _teardown_error_case() -> tuple[list[str], BaseException | None, bool]:
    events: list[str] = []

    class TrackingRequest(Request):
        def close(self) -> None:
            events.append("request-close")
            super().close()

    app = Flask(__name__)
    app.request_class = TrackingRequest

    @app.teardown_request
    def request_teardown(exc):
        events.append("request-teardown")
        raise RuntimeError("teardown boom")

    @app.teardown_appcontext
    def app_teardown(exc):
        events.append("app-teardown")

    ctx = app.test_request_context("/")
    ctx.push()
    caught: BaseException | None = None

    try:
        ctx.pop()
    except BaseException as exc:  # expected: collected and raised after cleanup
        caught = exc

    return events, caught, has_app_context()


def teardown_continuation(state: str) -> None:
    _assert_state(state)

    if state in {"primary", "edge", "exception"}:
        events, caught, still_active = _teardown_error_case()

        if state == "primary":
            assert "request-teardown" in events
            assert "request-close" in events, events
            assert not still_active, "context stayed active after teardown failure"
        elif state == "edge":
            assert "app-teardown" in events, events
            assert not still_active, "context stayed active after teardown failure"
        else:
            assert caught is not None, "teardown failure was swallowed"
            messages = _flatten_exception_messages(caught)
            assert any("teardown boom" in msg for msg in messages), messages
    else:  # regression
        events: list[str] = []

        class TrackingRequest(Request):
            def close(self) -> None:
                events.append("request-close")
                super().close()

        app = Flask(__name__)
        app.request_class = TrackingRequest

        @app.teardown_request
        def request_teardown(exc):
            events.append("request-teardown")

        @app.teardown_appcontext
        def app_teardown(exc):
            events.append("app-teardown")

        ctx = app.test_request_context("/")
        ctx.push()
        ctx.pop()
        assert events == ["request-teardown", "request-close", "app-teardown"], events
        assert not has_app_context()

    _pass(state)


def nested_context_lifetime(state: str) -> None:
    _assert_state(state)
    events: list[BaseException | None] = []
    app = Flask(__name__)

    @app.teardown_appcontext
    def teardown(exc):
        events.append(exc)

    ctx = app.app_context()

    if state == "primary":
        ctx.push()
        ctx.push()
        ctx.pop()
        assert has_app_context(), "first pop tore down a doubly-pushed context"
        assert events == [], events
        ctx.pop()
        assert not has_app_context()
        assert len(events) == 1

    elif state == "edge":
        ctx.push()
        ctx.push()
        ctx.push()
        ctx.pop()
        assert has_app_context() and events == []
        ctx.pop()
        assert has_app_context(), "cleanup happened before the final matching pop"
        assert events == []
        ctx.pop()
        assert not has_app_context()
        assert len(events) == 1

    elif state == "exception":
        marker = RuntimeError("outer failure")
        ctx.push()
        ctx.push()
        ctx.pop()
        assert has_app_context() and events == []
        ctx.pop(marker)
        assert len(events) == 1
        assert events[0] is marker
        assert not has_app_context()

    else:  # regression
        ctx.push()
        assert has_app_context()
        ctx.pop()
        assert not has_app_context()
        assert len(events) == 1

    _pass(state)


def error_handler_precedence(state: str) -> None:
    _assert_state(state)

    if state == "primary":
        app = Flask(__name__)
        bp = Blueprint("bp", __name__)

        @app.errorhandler(404)
        def app_404(exc):
            return "app-404", 404

        @bp.errorhandler(404)
        def bp_404(exc):
            return "bp-404", 404

        @bp.get("/missing")
        def missing():
            abort(404)

        app.register_blueprint(bp, url_prefix="/bp")
        response = app.test_client().get("/bp/missing")
        assert response.status_code == 404
        assert response.get_data(as_text=True) == "bp-404"

    elif state == "edge":
        app = Flask(__name__)
        bp = Blueprint("bp", __name__)

        @app.errorhandler(ValueError)
        def app_value(exc):
            return "app-value", 500

        @bp.errorhandler(ValueError)
        def bp_value(exc):
            return "bp-value", 500

        @bp.get("/boom")
        def boom():
            raise ValueError("boom")

        app.register_blueprint(bp, url_prefix="/bp")
        response = app.test_client().get("/bp/boom")
        assert response.get_data(as_text=True) == "bp-value"

    elif state == "exception":
        app = Flask(__name__)
        bp = Blueprint("bp", __name__)

        @app.errorhandler(Exception)
        def app_generic(exc):
            return "app-generic", 500

        @bp.errorhandler(KeyError)
        def bp_key(exc):
            return "bp-key", 500

        @bp.get("/boom")
        def boom():
            raise KeyError("boom")

        app.register_blueprint(bp, url_prefix="/bp")
        response = app.test_client().get("/bp/boom")
        assert response.get_data(as_text=True) == "bp-key"

    else:  # regression
        app = Flask(__name__)

        @app.errorhandler(ValueError)
        def app_value(exc):
            return "app-value", 500

        @app.get("/boom")
        def boom():
            raise ValueError("boom")

        response = app.test_client().get("/boom")
        assert response.get_data(as_text=True) == "app-value"

    _pass(state)


def deprecation_warning_text(state: str) -> None:
    _assert_state(state)

    if state == "regression":
        try:
            getattr(flask_ctx, "DefinitelyMissingContextName")
        except AttributeError:
            pass
        else:
            raise AssertionError("unknown flask.ctx attribute did not raise AttributeError")
        _pass(state)
        return

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        alias = getattr(flask_ctx, "RequestContext")

    messages = [str(item.message) for item in seen]

    if state == "primary":
        assert any("Use 'AppContext' instead." in msg for msg in messages), messages
        assert not any("Use 'Request' instead." in msg for msg in messages), messages
    elif state == "edge":
        assert alias is flask_ctx.AppContext
    else:  # exception
        assert any(issubclass(item.category, DeprecationWarning) for item in seen), seen

    _pass(state)


GRADERS: dict[str, Callable[[str], None]] = {
    "FL-SK01-T01": template_precedence,
    "FL-SK01-T02": request_limit_precedence,
    "FL-SK05-T01": teardown_continuation,
    "FL-SK07-T01": nested_context_lifetime,
    "FL-SK09-T01": error_handler_precedence,
    "FL-NS-01": deprecation_warning_text,
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
