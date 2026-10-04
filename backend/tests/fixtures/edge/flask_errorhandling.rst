Handling Application Errors
===========================

Applications fail, servers fail. Sooner or later you will see an exception
in production. Even if your code is 100% correct, you will still see
exceptions from time to time. Why? Because everything else involved will
fail. Here are some situations where perfectly fine code can lead to server
errors:

-   the client terminated the request early and the application was still
    reading from the incoming data
-   the database server was overloaded and could not handle the query
-   a filesystem is full
-   a hard drive crashed
-   a backend server overloaded
-   a programming error in a library you are using
-   network connection of the server to another system failed

And that's just a small sample of issues you could be facing. So how do we
deal with that sort of problem? By default if your application runs in
production mode, and an exception is raised Flask will display a very simple
page for you and log the exception to the :attr:`~flask.Flask.logger`.

But there is more you can do, and we will cover some better setups to deal
with errors including custom exceptions and 3rd party tools.


.. _error-logging-tools:

Error Logging Tools
-------------------

Sending error mails, even if just for critical ones, can become
overwhelming if enough users are hitting the error and log files are
typically never looked at. This is why we recommend using `Sentry
<https://sentry.io/>`_ for dealing with application errors. It's
available as a source-available project `on GitHub
<https://github.com/getsentry/sentry>`_ and is also available as a `hosted version
<https://sentry.io/signup/>`_ which you can try for free. Sentry
aggregates duplicate errors, captures the full stack trace and local
variables for debugging, and sends you mails based on new errors or
frequency thresholds.

To use Sentry you need to install the ``sentry-sdk`` client with extra
``flask`` dependencies.

.. code-block:: text

    $ pip install sentry-sdk[flask]

And then add this to your Flask app:

.. code-block:: python

    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init('YOUR_DSN_HERE', integrations=[FlaskIntegration()])

The ``YOUR_DSN_HERE`` value needs to be replaced with the DSN value you
get from your Sentry installation.

After installation, failures leading to an Internal Server Error
are automatically reported to Sentry and from there you can
receive error notifications.

See also:

-   Sentry also supports catching errors from a worker queue
    (RQ, Celery, etc.) in a similar fashion. See the `Python SDK docs
    <https://docs.sentry.io/platforms/python/>`__ for more information.
-   `Flask-specific documentation <https://docs.sentry.io/platforms/python/guides/flask/>`__


Error Handlers
--------------

When an error occurs in Flask, an appropriate `HTTP status code
<https://developer.mozilla.org/en-US/docs/Web/HTTP/Status>`__ will be
returned. 400-499 indicate errors with the client's request data, or
about the data requested. 500-599 indicate errors with the server or
application itself.

You might want to show custom error pages to the user when an error occurs.
This can be done by registering error handlers.

An error handler is a function that returns a response when a type of error is
raised, similar to how a view is a function that returns a response when a
request URL is matched. It is passed the instance of the error being handled,
which is most likely a :exc:`~werkzeug.exceptions.HTTPException`.

The status code of the response will not be set to the handler's code. Make
sure to provide the appropriate HTTP status code when returning a response from
a handler.


Registering
```````````

Register handlers by decorating a function with
:meth:`~flask.Flask.errorhandler`. Or use
:meth:`~flask.Flask.register_error_handler` to register the function later.
Remember to set the error code when returning the response.

.. code-block:: python

    @app.errorhandler(werkzeug.exceptions.BadRequest)
    def handle_bad_request(e):
        return 'bad request!', 400

    # or, without the decorator
    app.register_error_handler(400, handle_bad_request)

:exc:`werkzeug.exceptions.HTTPException` subclasses like
:exc:`~werkzeug.exceptions.BadRequest` and their HTTP codes are interchangeable
when registering handlers. (``BadRequest.code == 400``)

Non-standard HTTP codes cannot be registered by code because they are not known
by Werkzeug. Instead, define a subclass of
:class:`~werkzeug.exceptions.HTTPException` with the appropriate code and
register and raise that exception class.

.. code-block:: python

    class InsufficientStorage(werkzeug.exceptions.HTTPException):
        code = 507
        description = 'Not enough storage space.'

    app.register_error_handler(InsufficientStorage, handle_507)

    raise InsufficientStorage()

Handlers can be registered for any exception class, not just
:exc:`~werkzeug.exceptions.HTTPException` subclasses or HTTP status
codes. Handlers can be registered for a specific class, or for all subclasses
of a parent class.


Handling
````````
