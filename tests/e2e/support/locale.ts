/** es-CO / COP formatting helpers for assertions (C2-5: `1.234.567,89`). Amounts arrive as decimal strings. */
export function formatCOP(amount: string): string {
  const [int, frac = ''] = amount.split('.');
  const sign = int.startsWith('-') ? '-' : '';
  const digits = int.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const cents = (frac + '00').slice(0, 2);
  return `${sign}$ ${digits},${cents}`;
}
