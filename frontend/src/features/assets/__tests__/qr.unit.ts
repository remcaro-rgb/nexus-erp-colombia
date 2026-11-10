import { describe, expect, it } from "vitest";
import { chooseVersion, dataCodewords, encodeQr, formatBits, interleave, reedSolomon, utf8, versionBits } from "../qr";

describe("QR encoder (byte mode, level M)", () => {
  it("Reed–Solomon matches the published 1-M 'HELLO WORLD' vector", () => {
    const data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17];
    expect(reedSolomon(data, 10)).toEqual([196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
  });

  it("format and version information use the standard BCH codes", () => {
    expect(formatBits(0).toString(2).padStart(15, "0")).toBe("101010000010010"); // M, mask 0
    expect(formatBits(5).toString(2).padStart(15, "0")).toBe("100000011001110"); // M, mask 5
    expect(formatBits(7).toString(2).padStart(15, "0")).toBe("100101010100000"); // M, mask 7
    expect(versionBits(7).toString(2).padStart(18, "0")).toBe("000111110010010100");
  });

  it("chooses the smallest version and pads data with 0xEC/0x11", () => {
    expect(chooseVersion(14)).toBe(1);
    expect(chooseVersion(15)).toBe(2);
    expect(chooseVersion(106)).toBe(6);
    expect(() => chooseVersion(400)).toThrow(RangeError);
    const cw = dataCodewords(utf8("AB"), 1);
    expect(cw.slice(0, 4)).toEqual([0x40, 0x24, 0x14, 0x20]);
    expect(cw.slice(4, 6)).toEqual([0xec, 0x11]);
    expect(cw).toHaveLength(16);
    expect(interleave(cw, 1)).toHaveLength(26);
  });

  it("builds a symbol with finder, timing and dark-module patterns", () => {
    const qr = encodeQr("NXAF:LAP-001");
    expect(qr.version).toBe(1);
    expect(qr.size).toBe(21);
    const m = qr.modules;
    for (const [x0, y0] of [[0, 0], [14, 0], [0, 14]] as const) {
      for (let i = 0; i < 7; i++) {
        expect(m[y0]![x0 + i]).toBe(true);
        expect(m[y0 + 6]![x0 + i]).toBe(true);
      }
      expect(m[y0 + 1]![x0 + 1]).toBe(false);
      expect(m[y0 + 3]![x0 + 3]).toBe(true);
    }
    for (let i = 8; i < 13; i++) expect(m[6]![i]).toBe(i % 2 === 0);
    expect(m[qr.size - 8]![8]).toBe(true);
    // the format information around the top-left finder decodes to the chosen mask
    let bits = 0;
    for (let i = 0; i <= 5; i++) bits |= (m[i]![8] ? 1 : 0) << i;
    bits |= (m[7]![8] ? 1 : 0) << 6;
    bits |= (m[8]![8] ? 1 : 0) << 7;
    bits |= (m[8]![7] ? 1 : 0) << 8;
    for (let i = 9; i < 15; i++) bits |= (m[8]![14 - i] ? 1 : 0) << i;
    expect(bits).toBe(formatBits(qr.mask));
    expect(encodeQr("NXAF:LAP-001")).toEqual(qr); // deterministic
    expect(encodeQr("x".repeat(200)).version).toBe(10);
  });
});
