const filename = (header: string | null, fallback: string) =>
  header?.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
// Downloads need the auth header, so the body is fetched as a blob and saved from memory.
export async function saveResponse(response: Response, fallbackName: string) {
  const blob = await response.blob();
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = filename(
    response.headers.get("Content-Disposition"),
    fallbackName,
  );
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
