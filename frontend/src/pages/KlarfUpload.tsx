import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { IngestResult } from "../types";

export default function KlarfUpload() {
  const inputRef                  = useRef<HTMLInputElement>(null);
  const [file, setFile]           = useState<File | null>(null);
  const [result, setResult]       = useState<IngestResult | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [dragging, setDragging]   = useState(false);

  const handleFile = (f: File | undefined) => {
    if (!f) return;
    setFile(f);
    setResult(null);
    setError(null);
  };

  const upload = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.ingest.klarf2(file);
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-xl font-bold text-slate-100">Upload KLARF 2.0</h1>
      <p className="text-slate-400 text-sm">
        Upload a binary <code className="bg-slate-800 px-1.5 py-0.5 rounded text-emerald-400">.klf2</code> file
        to ingest wafer/panel defect data into OpenYield. Each wafer in the file becomes a panel record.
      </p>

      {/* Drop zone */}
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          e.preventDefault();
          setDragging(false);
          handleFile(e.dataTransfer.files[0]);
        }}
        className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
          dragging
            ? "border-emerald-500 bg-emerald-500/10"
            : file
            ? "border-slate-600 bg-slate-800/50"
            : "border-slate-700 bg-slate-900 hover:border-slate-600"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".klf2,.klarf"
          className="hidden"
          onChange={e => handleFile(e.target.files?.[0])}
        />

        {file ? (
          <div>
            <p className="text-emerald-400 font-medium">{file.name}</p>
            <p className="text-slate-500 text-sm mt-1">{(file.size / 1024).toFixed(1)} KB</p>
            <button
              onClick={e => { e.stopPropagation(); setFile(null); setResult(null); }}
              className="mt-3 text-slate-500 hover:text-slate-300 text-xs underline"
            >
              Remove
            </button>
          </div>
        ) : (
          <div>
            <p className="text-slate-400">Drop a <span className="text-slate-200">.klf2</span> file here</p>
            <p className="text-slate-600 text-sm mt-1">or click to browse</p>
          </div>
        )}
      </div>

      {/* Upload button */}
      {file && !result && (
        <button
          onClick={upload}
          disabled={loading}
          className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white
                     px-6 py-2.5 rounded-lg text-sm font-medium transition-colors w-full"
        >
          {loading ? "Ingesting…" : "Ingest File"}
        </button>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Success */}
      {result && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-5 space-y-4">
          <p className="text-emerald-400 font-semibold">Ingest complete</p>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="bg-slate-900 rounded-lg p-3">
              <p className="text-slate-500 text-xs">Lot ID</p>
              <p className="text-slate-200 font-mono mt-0.5">{result.lot_id}</p>
            </div>
            <div className="bg-slate-900 rounded-lg p-3">
              <p className="text-slate-500 text-xs">Wafers Ingested</p>
              <p className="text-emerald-400 font-bold text-xl mt-0.5">{result.wafers_ingested}</p>
            </div>
            <div className="bg-slate-900 rounded-lg p-3 col-span-2">
              <p className="text-slate-500 text-xs">Defects Inserted</p>
              <p className="text-red-400 font-bold text-xl mt-0.5">{result.defects_inserted.toLocaleString()}</p>
            </div>
          </div>

          {result.panel_ids.length > 0 && (
            <div>
              <p className="text-slate-500 text-xs mb-2">Created panels:</p>
              <div className="flex flex-wrap gap-2">
                {result.panel_ids.map(pid => (
                  <Link
                    key={pid}
                    to={`/yield-map?panel=${pid}`}
                    className="bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md
                               px-2.5 py-1 text-xs font-mono text-emerald-400 transition-colors"
                  >
                    {pid} →
                  </Link>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={() => { setFile(null); setResult(null); }}
            className="text-slate-500 hover:text-slate-300 text-xs underline"
          >
            Upload another file
          </button>
        </div>
      )}
    </div>
  );
}
