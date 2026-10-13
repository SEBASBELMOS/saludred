import { useEffect, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { imagingApi } from "../../api/imaging";
import type { StudyImages } from "../../api/types";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { Action, Loading, Modal } from "../../components/ui";
export function ImageCanvas({ instanceId }: { instanceId: string }) {
  const [url, setUrl] = useState("");
  const [loaded, setLoaded] = useState("");
  const [failed, setFailed] = useState("");
  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [zoom, setZoom] = useState(100);
  const [invert, setInvert] = useState(false);
  const preview = useQuery({
    queryKey: ["preview", instanceId],
    queryFn: ({ signal }) => imagingApi.preview(instanceId, signal),
    gcTime: 0,
  });
  useEffect(() => {
    if (!preview.data) {
      setUrl("");
      return;
    }
    const objectUrl = URL.createObjectURL(preview.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [preview.data]);
  return (
    <>
      {preview.error ? (
        <ErrorState error={preview.error} retry={preview.refetch} />
      ) : (
        <div
          className="relative max-h-[55dvh] min-h-56 overflow-auto rounded-lg bg-ink"
          aria-busy={!url || loaded !== url}
        >
          {(!url || loaded !== url) && (
            <div
              role="status"
              className="absolute inset-0 z-10 grid min-h-56 place-items-center overflow-hidden bg-ink"
            >
              <span className="relative rounded-lg bg-ink/80 px-3 py-2 text-sm text-white">
                {failed === url && url
                  ? "No se pudo mostrar la imagen."
                  : "Cargando imagen…"}
              </span>
              {failed === url && url && (
                <Action
                  secondary
                  label="Reintentar"
                  className="relative"
                  onClick={() => {
                    setFailed("");
                    void preview.refetch();
                  }}
                />
              )}
            </div>
          )}
          {url && (
            <img
              key={url}
              src={url}
              onLoad={() => setLoaded(url)}
              onError={() => setFailed(url)}
              alt="Vista previa del estudio"
              className={`block max-w-none origin-top-left transition-opacity duration-base ease-out ${loaded === url ? "opacity-100" : "opacity-0"} [filter:brightness(var(--image-brightness))_contrast(var(--image-contrast))_invert(var(--image-invert))] w-[var(--image-zoom)]`}
              style={
                {
                  "--image-brightness": brightness / 100,
                  "--image-contrast": contrast / 100,
                  "--image-invert": invert ? 1 : 0,
                  "--image-zoom": `${zoom}%`,
                } as CSSProperties
              }
            />
          )}
        </div>
      )}
      <div className="my-4 grid gap-4 sm:grid-cols-3">
        {[
          {
            label: "Brillo",
            value: brightness,
            set: setBrightness,
            min: 40,
            max: 250,
          },
          {
            label: "Contraste",
            value: contrast,
            set: setContrast,
            min: 40,
            max: 300,
          },
          { label: "Zoom", value: zoom, set: setZoom, min: 100, max: 400 },
        ].map((control) => (
          <label key={control.label} className="text-sm font-semibold">
            {control.label}: {control.value} %
            <input
              className="mt-2 block w-full accent-accent"
              type="range"
              min={control.min}
              max={control.max}
              value={control.value}
              onChange={(event) => control.set(Number(event.target.value))}
            />
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="accent-accent"
            checked={invert}
            onChange={(event) => setInvert(event.target.checked)}
          />
          Invertir
        </label>
        <Action
          secondary
          label="Restablecer"
          onClick={() => {
            setBrightness(100);
            setContrast(100);
            setZoom(100);
            setInvert(false);
          }}
        />
      </div>
      <p className="mt-4 text-xs leading-relaxed text-ink-muted">
        Vista previa de 8 bits generada por el PACS: sirve para revisar el
        flujo, no para diagnóstico. Un estudio real se lee con ventaneo sobre
        los 12–16 bits originales.
      </p>
    </>
  );
}
function ImagePicker({ images }: { images: StudyImages["images"] }) {
  const [selected, setSelected] = useState(images[0].instance_id);
  return (
    <>
      <ImageCanvas instanceId={selected} />
      {images.length > 1 && (
        <div
          className="mt-4 flex flex-wrap gap-2"
          aria-label="Imágenes del estudio"
        >
          {images.map((image, index) => (
            <Action
              key={image.instance_id}
              secondary={selected !== image.instance_id}
              label={`Imagen ${image.number ?? index + 1}`}
              aria-pressed={selected === image.instance_id}
              onClick={() => setSelected(image.instance_id)}
            />
          ))}
        </div>
      )}
    </>
  );
}
export function ImageViewer({
  studyId,
  title,
  onClose,
}: {
  studyId: string;
  title: string;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["study-images", studyId],
    queryFn: ({ signal }) => imagingApi.images(studyId, signal),
  });
  return (
    <Modal title={title || "Imágenes del estudio"} onClose={onClose} wide>
      {query.error ? (
        <ErrorState error={query.error} retry={query.refetch} />
      ) : !query.data ? (
        <Loading />
      ) : query.data.images.length ? (
        <ImagePicker images={query.data.images} />
      ) : (
        <EmptyState title="Este estudio no tiene imágenes cargadas">
          Los metadatos existen, pero todavía no se subieron los píxeles al
          servidor de imágenes.
        </EmptyState>
      )}
      <div className="mt-5 flex justify-end">
        <Action secondary label="Volver" onClick={onClose} />
      </div>
    </Modal>
  );
}
