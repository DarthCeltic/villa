export interface DeclaredReceipt<T> {
  key: string;
  present: boolean;
  content: T | null;
  missing_reason?: string | null;
  produced_by?: string;
  sha256_16?: string | null;
}

export interface DeclaredReceiptResponse<T> {
  receipts: Record<string, DeclaredReceipt<T>>;
}

export function declaredReceiptUrl(key: string): string {
  return `/api/receipts?key=${encodeURIComponent(key)}`;
}

export function declaredReceipt<T>(
  payload: DeclaredReceiptResponse<T> | null | undefined,
  key: string,
): DeclaredReceipt<T> | null {
  return payload?.receipts?.[key] ?? null;
}
