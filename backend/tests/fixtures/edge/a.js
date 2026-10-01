// top comment
import fs from 'fs';

/** Adds two numbers. */
export default function add(a, b) { return a + b; }

export const handler = async (req) => { return 1; };

module.exports = { foo: 1 };

class Counter {
  #secret = 1;
  handleClick = () => { this.x++; };
  get value() { return this.#secret; }
  static async create() { return new Counter(); }
}
export default class Foo { m() {} }
const x = 5;
function outer() { function inner() {} return inner; }
