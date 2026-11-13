import { ApiError } from "../api/client";

export function describeError(error: unknown): { message: string; code?: string; id?: string } {
  if (error instanceof ApiError) {
    const result: { message: string; code?: string; id?: string } = {
      message: error.message,
      code: error.code,
    };
    if (error.correlationId) result.id = error.correlationId;
    return result;
  }
  if (error instanceof Error) return { message: error.message };
  return { message: "Неизвестная ошибка." };
}

export function ErrorNotice({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (error === null || error === undefined) return null;
  const { message, code, id } = describeError(error);
  return (
    <div className="notice notice-error" role="alert">
      <strong>Ошибка:</strong> {message}
      {code ? <span className="meta"> Код: {code}.</span> : null}
      {id ? <span className="meta"> ID для диагностики: {id}</span> : null}
      {onDismiss ? (
        <button type="button" className="link" onClick={onDismiss}>
          Скрыть
        </button>
      ) : null}
    </div>
  );
}
