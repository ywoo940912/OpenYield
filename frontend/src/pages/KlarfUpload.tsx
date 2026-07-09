import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { IngestResult } from "../types";

// ── Shared drop zone ──────────────────────────────────────────────────────────

function DropZone({
  file, accept, onFile, onClear,
}: {
  file: File | null;
  accept: string;
  onFile: (f: File) => void;
  onClear: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}
      className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
        dragging ? "border-emerald-500 bg-emerald-500/10" :
        file     ? "border-slate-600 bg-slate-800/50" :
                   "border-slate-700 bg-slate-900 hover:border-slate-600"
      }`}
    >
      <input ref={inputRef} type="file" accept={accept} className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); }} />
      {file ? (
        <div>
          <p className="text-emerald-400 font-medium text-sm">{file.name}</p>
          <p className="text-slate-500 text-xs mt-1">{(file.size / 1024).toFixed(1)} KB</p>
          <button onClick={e => { e.stopPropagation(); onClear(); }}
            className="mt-3 text-slate-500 hover:text-slate-300 text-xs underline">
            Remove
          </button>
        </div>
      ) : (
        <div>
          <p className="text-slate-400 text-sm">Drop file here</p>
          <p className="text-slate-600 text-xs mt-1">or click to browse</p>
        </div>
      )}
    </div>
  );
}

// ── KLARF 2.0 tab ─────────────────────────────────────────────────────────────

function KlarfTab() {
  const [file,    setFile]    = useState<File | null>(null);
  const [result,  setResult]  = useState<IngestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const upload = async () => {
    if (!file) return;
    setLoading(true); setError(null); setResult(null);
    try {
      setResult(await api.ingest.klarf2(file));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-slate-400 text-sm">
        Upload a binary <code className="bg-slate-800 px-1.5 py-0.5 rounded text-emerald-400">.klf2</code> file
        from KLA-Tencor or compatible tools. Each wafer in the file becomes a panel record.
      </p>
      <DropZone file={file} accept=".klf2,.klarf"
        onFile={f => { setFile(f); setResult(null); setError(null); }}
        onClear={() => { setFile(null); setResult(null); }} />
      {file && !result && (
        <button onClick={upload} disabled={loading}
          className="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium transition-colors">
          {loading ? "Ingesting…" : "Ingest File"}
        </button>
      )}
      {error && <ErrorBox msg={error} />}
      {result && <IngestSuccess result={result} onReset={() => { setFile(null); setResult(null); }} />}
    </div>
  );
}

// ── Flex CSV tab ──────────────────────────────────────────────────────────────

const EXAMPLE_CONFIG = JSON.stringify({
  "encoding": "utf-8-sig",
  "delimiter": ",",
  "skip_rows": 0,
  "substrate_type": "wafer",
  "panel_id":         { "template": "{LOT_ID}_{WAFER_ID}" },
  "component_row":    { "column": "DIE_ROW",  "type": "int" },
  "component_col":    { "column": "DIE_COL",  "type": "int" },
  "source_system":    { "value":  "system_a" },
  "defect_type": {
    "column": "CLASS_CODE",
    "map": { "0": "particle", "1": "scratch", "2": "pit", "3": "void" },
    "default": "unclassified"
  },
  "x":                { "column": "X_UM",    "scale": 0.001 },
  "y":                { "column": "Y_UM",    "scale": 0.001 },
  "size":             { "column": "SIZE_UM", "scale": 0.001 },
  "confidence_score": { "value": 0.75 }
}, null, 2);

function FlexCsvTab() {
  const [file,    setFile]    = useState<File | null>(null);
  const [config,  setConfig]  = useState(EXAMPLE_CONFIG);
  const [result,  setResult]  = useState<{ records_ingested: number; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [configErr, setConfigErr] = useState<string | null>(null);

  function validateConfig(text: string) {
    try { JSON.parse(text); setConfigErr(null); }
    catch (e) { setConfigErr("Invalid JSON — " + (e instanceof Error ? e.message : String(e))); }
  }

  const upload = async () => {
    if (!file || configErr) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("config", config);
      const res = await fetch("/ingest/flex-csv", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      setResult(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="bg-slate-800/50 border border-slate-700 rounded-xl px-4 py-3 text-xs text-slate-400 space-y-1">
        <p className="font-medium text-slate-300">For any inspection equipment that exports CSV</p>
        <p>Edit the mapping config below to match your column names, units, and class codes — no code required.
           For complex formats, subclass <code className="text-emerald-400">BaseAdapter</code> in
           <code className="text-emerald-400"> openyield/ingestion/adapters/</code>.</p>
      </div>

      {/* CSV file */}
      <div>
        <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2 block">
          1. Your CSV file
        </label>
        <DropZone file={file} accept=".csv,.tsv,.txt"
          onFile={f => { setFile(f); setResult(null); setError(null); }}
          onClear={() => { setFile(null); setResult(null); }} />
      </div>

      {/* Mapping config */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            2. Column mapping config (JSON)
          </label>
          <button onClick={() => setConfig(EXAMPLE_CONFIG)}
            className="text-xs text-slate-500 hover:text-slate-300 underline">
            reset to example
          </button>
        </div>
        <textarea
          value={config}
          onChange={e => { setConfig(e.target.value); validateConfig(e.target.value); }}
          rows={18}
          spellCheck={false}
          className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-xs font-mono text-slate-300 focus:outline-none focus:border-emerald-500 resize-y"
        />
        {configErr && (
          <p className="text-red-400 text-xs mt-1">{configErr}</p>
        )}
      </div>

      <button
        onClick={upload}
        disabled={!file || !!configErr || loading}
        className="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium transition-colors"
      >
        {loading ? "Ingesting…" : "Ingest CSV"}
      </button>

      {error && <ErrorBox msg={error} />}

      {result && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-5 space-y-3">
          <p className="text-emerald-400 font-semibold text-sm">Ingest complete</p>
          <div className="bg-slate-900 rounded-lg p-3">
            <p className="text-slate-500 text-xs">Records ingested</p>
            <p className="text-emerald-400 font-bold text-2xl mt-0.5">{result.records_ingested.toLocaleString()}</p>
          </div>
          <button onClick={() => { setFile(null); setResult(null); }}
            className="text-slate-500 hover:text-slate-300 text-xs underline">
            Upload another file
          </button>
        </div>
      )}

      {/* Spec reference */}
      <details className="group">
        <summary className="text-xs text-slate-500 hover:text-slate-300 cursor-pointer select-none">
          Mapping spec reference ▸
        </summary>
        <div className="mt-3 space-y-2 text-xs text-slate-400">
          {[
            ['Column + scale', '{"column": "X_UM", "scale": 0.001}', 'Read column, multiply by scale'],
            ['Fixed value',    '{"value": "system_a"}',              'Always use this literal value'],
            ['Class map',      '{"column": "CLASS", "map": {"1": "particle"}, "default": "unclassified"}', 'Map codes to defect type strings'],
            ['Template',       '{"template": "{LOT_ID}_{WAFER_ID}"}', 'Build value from multiple columns'],
          ].map(([name, code, desc]) => (
            <div key={name} className="bg-slate-900 rounded-lg px-3 py-2">
              <p className="text-slate-300 font-medium">{name}</p>
              <code className="text-emerald-400 block mt-0.5">{code}</code>
              <p className="text-slate-600 mt-0.5">{desc}</p>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

// ── Shared result components ───────────────────────────────────────────────────

function IngestSuccess({ result, onReset }: { result: IngestResult; onReset: () => void }) {
  return (
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
              <Link key={pid} to={`/yield-map?panel=${pid}`}
                className="bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md px-2.5 py-1 text-xs font-mono text-emerald-400 transition-colors">
                {pid} →
              </Link>
            ))}
          </div>
        </div>
      )}
      <button onClick={onReset} className="text-slate-500 hover:text-slate-300 text-xs underline">
        Upload another file
      </button>
    </div>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-400 text-sm whitespace-pre-wrap">
      {msg}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = "klarf" | "flex-csv";

export default function KlarfUpload() {
  const [tab, setTab] = useState<Tab>("klarf");

  return (
    <div className="space-y-5 max-w-2xl">
      <div>
        <h1 className="text-xl font-bold text-slate-100">Import Inspection Data</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Ingest defect records from KLA KLARF files or any CSV export from third-party equipment
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {([["klarf", "KLARF 2.0"], ["flex-csv", "Flex CSV"]] as [Tab, string][]).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-md text-xs font-medium transition-colors ${
              tab === t ? "bg-emerald-600 text-white" : "text-slate-400 hover:text-slate-100"
            }`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "klarf"    && <KlarfTab />}
      {tab === "flex-csv" && <FlexCsvTab />}
    </div>
  );
}
