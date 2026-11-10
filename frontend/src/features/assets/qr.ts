// QR Code encoder (ISO/IEC 18004), owner P14 — byte mode, error-correction level M, versions 1–10.
// Used for the asset tag labels (payload: the asset tag). No runtime dependency; pure functions, tested in
// __tests__/qr.unit.ts against published Reed–Solomon and format-information vectors.

/** [total codewords, EC codewords per block, blocks in group 1, data codewords per block g1, blocks g2, data g2] */
const LEVEL_M: ReadonlyArray<readonly [number, number, number, number, number, number]> = [
  [26, 10, 1, 16, 0, 0],
  [44, 16, 1, 28, 0, 0],
  [70, 26, 1, 44, 0, 0],
  [100, 18, 2, 32, 0, 0],
  [134, 24, 2, 43, 0, 0],
  [172, 16, 4, 27, 0, 0],
  [196, 18, 4, 31, 0, 0],
  [242, 22, 2, 38, 2, 39],
  [292, 22, 3, 36, 2, 37],
  [346, 26, 4, 43, 1, 44],
];
const ALIGNMENT: ReadonlyArray<readonly number[]> = [[], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
export const MAX_VERSION = LEVEL_M.length;

// ---------------------------------------------------------------- GF(256) / Reed–Solomon (poly 0x11D)
const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
{
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]!;
}

function gfMul(a: number, b: number): number {
  return a === 0 || b === 0 ? 0 : EXP[LOG[a]! + LOG[b]!]!;
}

function generator(degree: number): number[] {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] = next[j]! ^ poly[j]!;
      next[j + 1] = next[j + 1]! ^ gfMul(poly[j]!, EXP[i]!);
    }
    poly = next;
  }
  return poly;
}

/** Reed–Solomon error-correction codewords of `data` (highest-degree coefficient first). */
export function reedSolomon(data: readonly number[], degree: number): number[] {
  const gen = generator(degree);
  const rem = [...data, ...new Array<number>(degree).fill(0)];
  for (let i = 0; i < data.length; i++) {
    const coef = rem[i]!;
    if (coef === 0) continue;
    for (let j = 0; j < gen.length; j++) rem[i + j] = rem[i + j]! ^ gfMul(gen[j]!, coef);
  }
  return rem.slice(data.length);
}

// ---------------------------------------------------------------- data encoding

export function utf8(text: string): number[] {
  return Array.from(new TextEncoder().encode(text));
}

function dataCapacity(version: number): number {
  const [, , b1, d1, b2, d2] = LEVEL_M[version - 1]!;
  return b1 * d1 + b2 * d2;
}

export function chooseVersion(byteLength: number): number {
  for (let v = 1; v <= MAX_VERSION; v++) {
    const countBits = v < 10 ? 8 : 16;
    if (4 + countBits + byteLength * 8 <= dataCapacity(v) * 8) return v;
  }
  throw new RangeError(`QR payload too long (${byteLength} bytes; max version ${MAX_VERSION}-M)`);
}

/** Data codewords (mode, count, bytes, terminator, padding) for `bytes` at `version`. */
export function dataCodewords(bytes: readonly number[], version: number): number[] {
  const bits: number[] = [];
  const put = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  put(0b0100, 4);
  put(bytes.length, version < 10 ? 8 : 16);
  for (const b of bytes) put(b, 8);
  const capacity = dataCapacity(version) * 8;
  put(0, Math.min(4, capacity - bits.length));
  while (bits.length % 8) bits.push(0);
  const out: number[] = [];
  for (let i = 0; i < bits.length; i += 8) out.push(bits.slice(i, i + 8).reduce((acc, b) => (acc << 1) | b, 0));
  for (let pad = 0xec; out.length < dataCapacity(version); pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

/** Splits into blocks, appends EC codewords and interleaves (final codeword sequence). */
export function interleave(data: readonly number[], version: number): number[] {
  const [, ec, b1, d1, b2, d2] = LEVEL_M[version - 1]!;
  const blocks: number[][] = [];
  let offset = 0;
  for (let i = 0; i < b1 + b2; i++) {
    const len = i < b1 ? d1 : d2;
    blocks.push(data.slice(offset, offset + len));
    offset += len;
  }
  const ecc = blocks.map((b) => reedSolomon(b, ec));
  const out: number[] = [];
  const maxLen = Math.max(d1, d2);
  for (let i = 0; i < maxLen; i++) for (const b of blocks) if (i < b.length) out.push(b[i]!);
  for (let i = 0; i < ec; i++) for (const e of ecc) out.push(e[i]!);
  return out;
}

// ---------------------------------------------------------------- matrix

/** 15-bit format information for level M (bits 00) and `mask`, BCH(15,5) + XOR 0x5412. */
export function formatBits(mask: number): number {
  const data = (0b00 << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

export function versionBits(version: number): number {
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

export interface QrMatrix {
  version: number;
  size: number;
  mask: number;
  /** modules[y][x] === true → dark */
  modules: boolean[][];
}

class Builder {
  readonly size: number;
  readonly modules: boolean[][];
  readonly fn: boolean[][];

  constructor(readonly version: number) {
    this.size = version * 4 + 17;
    this.modules = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
    this.fn = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
  }

  set(x: number, y: number, dark: boolean): void {
    this.modules[y]![x] = dark;
    this.fn[y]![x] = true;
  }

  functionPatterns(): void {
    const s = this.size;
    for (let i = 0; i < s; i++) {
      this.set(6, i, i % 2 === 0);
      this.set(i, 6, i % 2 === 0);
    }
    for (const [cx, cy] of [[3, 3], [s - 4, 3], [3, s - 4]] as const) {
      for (let dy = -4; dy <= 4; dy++)
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx;
          const y = cy + dy;
          if (x < 0 || y < 0 || x >= s || y >= s) continue;
          const d = Math.max(Math.abs(dx), Math.abs(dy));
          this.set(x, y, d !== 2 && d !== 4);
        }
    }
    const pos = ALIGNMENT[this.version - 1]!;
    const last = pos.length - 1;
    pos.forEach((cy, i) =>
      pos.forEach((cx, j) => {
        if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
        for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) this.set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }),
    );
    this.format(0);
    if (this.version >= 7) {
      const bits = versionBits(this.version);
      for (let i = 0; i < 18; i++) {
        const bit = ((bits >>> i) & 1) === 1;
        const a = s - 11 + (i % 3);
        const b = Math.floor(i / 3);
        this.set(a, b, bit);
        this.set(b, a, bit);
      }
    }
  }

  format(mask: number): void {
    const bits = formatBits(mask);
    const bit = (i: number) => ((bits >>> i) & 1) === 1;
    const s = this.size;
    for (let i = 0; i <= 5; i++) this.set(8, i, bit(i));
    this.set(8, 7, bit(6));
    this.set(8, 8, bit(7));
    this.set(7, 8, bit(8));
    for (let i = 9; i < 15; i++) this.set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) this.set(s - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) this.set(8, s - 15 + i, bit(i));
    this.set(8, s - 8, true); // dark module
  }

  codewords(data: readonly number[]): void {
    const s = this.size;
    let i = 0;
    for (let right = s - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < s; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? s - 1 - vert : vert;
          if (!this.fn[y]![x] && i < data.length * 8) {
            this.modules[y]![x] = ((data[i >>> 3]! >>> (7 - (i & 7))) & 1) === 1;
            i++;
          }
        }
      }
    }
  }

  applyMask(mask: number): void {
    for (let y = 0; y < this.size; y++)
      for (let x = 0; x < this.size; x++) {
        if (this.fn[y]![x]) continue;
        let invert: boolean;
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break;
          case 1: invert = y % 2 === 0; break;
          case 2: invert = x % 3 === 0; break;
          case 3: invert = (x + y) % 3 === 0; break;
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: invert = ((x * y) % 2) + ((x * y) % 3) === 0; break;
          case 6: invert = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0; break;
          default: invert = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
        }
        if (invert) this.modules[y]![x] = !this.modules[y]![x];
      }
  }

  /** Simplified penalty (rules 1–4 of ISO/IEC 18004 §8.8.2) to pick the mask. */
  penalty(): number {
    const s = this.size;
    const m = this.modules;
    let score = 0;
    const runs = (get: (i: number, j: number) => boolean) => {
      for (let i = 0; i < s; i++) {
        let run = 1;
        for (let j = 1; j <= s; j++) {
          if (j < s && get(i, j) === get(i, j - 1)) run++;
          else {
            if (run >= 5) score += run - 2;
            run = 1;
          }
        }
        for (let j = 0; j + 6 < s; j++) {
          const p = [1, 0, 1, 1, 1, 0, 1].every((v, k) => get(i, j + k) === (v === 1));
          if (p) score += 40;
        }
      }
    };
    runs((y, x) => m[y]![x]!);
    runs((x, y) => m[y]![x]!);
    for (let y = 0; y + 1 < s; y++)
      for (let x = 0; x + 1 < s; x++) {
        const c = m[y]![x]!;
        if (c === m[y]![x + 1] && c === m[y + 1]![x] && c === m[y + 1]![x + 1]) score += 3;
      }
    const dark = m.reduce((acc, row) => acc + row.filter(Boolean).length, 0);
    score += Math.floor(Math.abs((dark * 20) / (s * s) - 10)) * 10;
    return score;
  }
}

/** Encodes `text` (UTF-8 bytes) as a QR symbol, level M; the mask with the lowest penalty is chosen. */
export function encodeQr(text: string, forceMask?: number): QrMatrix {
  const bytes = utf8(text);
  const version = chooseVersion(bytes.length);
  const codewords = interleave(dataCodewords(bytes, version), version);
  let best: { mask: number; modules: boolean[][]; score: number } | null = null;
  for (let mask = 0; mask < 8; mask++) {
    if (forceMask !== undefined && mask !== forceMask) continue;
    const b = new Builder(version);
    b.functionPatterns();
    b.codewords(codewords);
    b.applyMask(mask);
    b.format(mask);
    const score = b.penalty();
    if (!best || score < best.score) best = { mask, modules: b.modules, score };
  }
  return { version, size: version * 4 + 17, mask: best!.mask, modules: best!.modules };
}
