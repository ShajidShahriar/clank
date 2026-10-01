import type { A } from './a';
import fs = require('fs');

/** Props for the button. */
export interface Props extends Base {
  id: number;
  onClick?(e: Event): void;
}

type Pair<T> = [T, T];
export type Id = string | number;

enum Color { Red, Green }
export const enum Dir { Up }

declare function ext(x: number): void;

function over(a: string): string;
function over(a: number): number;
function over(a: any) { return a; }

/** Base shape. */
abstract class Shape<T> extends Base implements I {
  private count: number = 0;
  public handle = (e: Event): void => { this.count++; };
  static create(): Shape<any> { return null as any; }
  abstract area(): number;
  get v(): number { return 1; }
  method(a: string): void;
  method(a: any) {}
}

@Component({ selector: 'x' })
export class Cmp {
  constructor(private a: number) {}
}

namespace NS {
  export function f() {}
  export interface Inner { y: number }
}

declare module 'ext-lib' {
  export const y: number;
}

declare global {
  interface Window { z: number }
}

export const App: React.FC<Props> = (props) => { return 1; };
export default function main(): void {}
