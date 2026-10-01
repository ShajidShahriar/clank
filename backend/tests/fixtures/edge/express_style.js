'use strict';
var path = require('path');
var extname = path.extname;

/**
 * Send a response.
 */
res.send = function send(body) {
  return this.end(body);
};

app.use = function use(fn) {
  return this;
};

exports.render = function (view) {
  return view;
};

module.exports.helper = (a) => a + 1;
