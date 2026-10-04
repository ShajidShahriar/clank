Blueprint Error Handlers
------------------------

In :doc:`/blueprints`, most error handlers will work as expected.
However, there is a caveat concerning handlers for 404 and 405
exceptions. These error handlers are only invoked from an appropriate
``raise`` statement or a call to ``abort`` in another of the blueprint's
view functions; they are not invoked by, e.g., an invalid URL access.

This is because the blueprint does not "own" a certain URL space, so
the application instance has no way of knowing which blueprint error
handler it should run if given an invalid URL. If you would like to
execute different handling strategies for these errors based on URL
prefixes, they may be defined at the application level using the
``request`` proxy object.

.. code-block:: python

    from flask import jsonify, render_template

    # at the application level
    # not the blueprint level
    @app.errorhandler(404)
    def page_not_found(e):
        # if a request is in our blog URL space
        if request.path.startswith('/blog/'):
            # we return a custom blog 404 page
            return render_template("blog/404.html"), 404
        else:
            # otherwise we return our generic site-wide 404 page
            return render_template("404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        # if a request has the wrong method to our API
        if request.path.startswith('/api/'):
            # we return a json saying so
            return jsonify(message="Method Not Allowed"), 405
        else:
            # otherwise we return a generic site-wide 405 page
            return render_template("405.html"), 405


Returning API Errors as JSON
----------------------------

When building APIs in Flask, some developers realise that the built-in
exceptions are not expressive enough for APIs and that the content type of
:mimetype:`text/html` they are emitting is not very useful for API consumers.

