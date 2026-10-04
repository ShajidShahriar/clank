Version 3.2.0
-------------

Unreleased

-   Drop support for Python 3.9. :pr:`5730`
-   Remove previously deprecated code: ``__version__``. :pr:`5648`
-   ``RequestContext`` has merged with ``AppContext``. ``RequestContext`` is now
    a deprecated alias. If an app context is already pushed, it is not reused
    when dispatching a request. This greatly simplifies the internal code for tracking
    the active context. :issue:`5639`
-   Many ``Flask`` methods involved in request dispatch now take the current
    ``AppContext`` as the first parameter, instead of using the proxy objects.
    If subclasses were overriding these methods, the old signature is detected,
    shows a deprecation warning, and will continue to work during the
    deprecation period. :issue:`5815`
-   All teardown callbacks are called, even if any raise an error. :pr:`5928`
-   The ``should_ignore_error`` is deprecated. Handle errors as needed in
    teardown handlers instead. :issue:`5816`
-   ``template_filter``, ``template_test``, and ``template_global`` decorators
    can be used without parentheses. :issue:`5729`
-   ``redirect`` returns a ``303`` status code by default instead of ``302``.
    This tells the client to always switch to ``GET``, rather than only
    switching ``POST`` to ``GET``. This preserves the current behavior of
    ``GET`` and ``POST`` redirects, and is also correct for frontend libraries
    such as HTMX. :issue:`5895`
-   ``provide_automatic_options=True`` can be used to enable it for a view when
    it's disabled in config. Previously, only disabling worked. :issue:`5916`
-   ``Flask.select_jinja_autoescape`` uses case-insensitive comparison instead
    of only lower case file extensions. :pr:`6012`
-   Fix parsing IPv6 with port in ``run`` and the test client. :pr:`6096`
-   Add ``app.query`` route decorator for the HTTP QUERY method.


Version 3.1.3
-------------

Released 2026-02-18

-   The session is marked as accessed for operations that only access the keys
    but not the values, such as ``in`` and ``len``. :ghsa:`68rp-wp8r-4726`


Version 3.1.2
-------------

Released 2025-08-19

-   ``stream_with_context`` does not fail inside async views. :issue:`5774`
-   When using ``follow_redirects`` in the test client, the final state
    of ``session`` is correct. :issue:`5786`
-   Relax type hint for passing bytes IO to ``send_file``. :issue:`5776`


Version 3.1.1
-------------

Released 2025-05-13

-   Fix signing key selection order when key rotation is enabled via
    ``SECRET_KEY_FALLBACKS``. :ghsa:`4grg-w6v8-c28g`
-   Fix type hint for ``cli_runner.invoke``. :issue:`5645`
-   ``flask --help`` loads the app and plugins first to make sure all commands
    are shown. :issue:`5673`
-   Mark sans-io base class as being able to handle views that return
    ``AsyncIterable``. This is not accurate for Flask, but makes typing easier
    for Quart. :pr:`5659`


Version 3.1.0
-------------

Released 2024-11-13

-   Drop support for Python 3.8. :pr:`5623`
-   Update minimum dependency versions to latest feature releases.
    Werkzeug >= 3.1, ItsDangerous >= 2.2, Blinker >= 1.9. :pr:`5624,5633`
-   Provide a configuration option to control automatic option
    responses. :pr:`5496`
-   ``Flask.open_resource``/``open_instance_resource`` and
