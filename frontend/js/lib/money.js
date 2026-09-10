// Money helpers. All amounts move through the app as integer minor units
// (cents, yen, ...). Never store or math on floats.

const CURRENCIES = [
  { code: 'USD', label: 'US Dollar', decimals: 2 },
  { code: 'EUR', label: 'Euro', decimals: 2 },
  { code: 'GBP', label: 'British Pound', decimals: 2 },
  { code: 'CAD', label: 'Canadian Dollar', decimals: 2 },
  { code: 'AUD', label: 'Australian Dollar', decimals: 2 },
  { code: 'INR', label: 'Indian Rupee', decimals: 2 },
  { code: 'JPY', label: 'Japanese Yen', decimals: 0 },
];

export const currencyList = CURRENCIES.slice();

export function currencyInfo(code) {
  return CURRENCIES.find((c) => c.code === code) || CURRENCIES[0];
}

/** Format integer minor units as a localized currency string. */
export function formatMoney(minor, code) {
  const { decimals } = currencyInfo(code);
  const value = minor / 10 ** decimals;
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: code,
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(value);
  } catch {
    return `${value.toFixed(decimals)} ${code}`;
  }
}

/** Signed amount with an explicit +/- and no currency symbol clutter. */
export function formatSigned(minor, code) {
  const s = formatMoney(Math.abs(minor), code);
  if (minor > 0) return `+${s}`;
  if (minor < 0) return `-${s}`;
  return s;
}

/**
 * Parse a user-typed amount into minor units.
 * @returns {{ok:true, minor:number} | {ok:false, error:string}}
 */
export function parseMoneyToMinor(input, code) {
  const { decimals } = currencyInfo(code);
  const s = String(input ?? '').trim().replace(/[,\s]/g, '');
  if (s === '') return { ok: false, error: 'Enter an amount' };
  if (!/^-?\d*(\.\d*)?$/.test(s) || s === '.' || s === '-') {
    return { ok: false, error: 'Not a valid number' };
  }
  const neg = s.startsWith('-');
  const [intPart = '0', fracPart = ''] = s.replace('-', '').split('.');
  if (fracPart.length > decimals) {
    return {
      ok: false,
      error: decimals === 0
        ? `${code} amounts have no decimal places`
        : `Max ${decimals} decimal places for ${code}`,
    };
  }
  const scaledFrac = decimals === 0 ? 0 : Number((fracPart + '0'.repeat(decimals)).slice(0, decimals));
  const minor = Number(intPart || '0') * 10 ** decimals + scaledFrac;
  return { ok: true, minor: (neg ? -1 : 1) * minor };
}

/** Inverse of parseMoneyToMinor, for prefilling inputs. */
export function minorToInput(minor, code) {
  const { decimals } = currencyInfo(code);
  const sign = minor < 0 ? '-' : '';
  const abs = Math.abs(minor);
  if (decimals === 0) return `${sign}${abs}`;
  const whole = Math.floor(abs / 10 ** decimals);
  const frac = String(abs % 10 ** decimals).padStart(decimals, '0');
  return `${sign}${whole}.${frac}`;
}
