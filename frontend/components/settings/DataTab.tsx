"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ErrorCallout } from "@/components/ui/Feedback";
import { ApiRequestError } from "@/lib/api/http";
import { exportData, getUploadInventory, type UploadInventory } from "@/lib/api/admin";
import { formatRelativeTime } from "@/lib/utils/format";

function size(bytes: number | null) {
  if (bytes === null) return "Not found";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DataTab({ role }: { role: string }) {
  const admin = role === "owner" || role === "admin";
  const [inventory, setInventory] = useState<UploadInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [exportError, setExportError] = useState<string | null>(null);
  const [exported, setExported] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!admin) return;
    getUploadInventory().then(setInventory).catch(() => setError("Could not load uploaded files. Try again."));
  }, [admin]);

  if (!admin) return <p className="settings-note">Only an owner or admin can manage data.</p>;

  async function runExport(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setExportError(null); setExported(false);
    try {
      const { blob, filename } = await exportData(password);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename ?? "optisynth-export.json";
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      setPassword(""); setExported(true);
    } catch (err) { setExportError(err instanceof ApiRequestError ? err.message : "Could not export. Try again."); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <Card padding="lg">
        <CardHeader><CardTitle>Uploaded evidence</CardTitle></CardHeader>
        {error && <ErrorCallout message={error} />}
        {inventory && inventory.files.length === 0 && <p className="settings-note">No evidence has been uploaded.</p>}
        {inventory && inventory.files.length > 0 && (
          <>
            <p className="settings-lead">{inventory.files.length} {inventory.files.length === 1 ? "file" : "files"}, {size(inventory.total_bytes)} in total. To remove evidence, delete the program it belongs to.</p>
            <div className="settings-table-wrap">
              <table className="settings-table">
                <thead><tr><th>File</th><th>Program</th><th>Rows</th><th>Size</th><th>Uploaded by</th><th>When</th></tr></thead>
                <tbody>
                  {inventory.files.map((f) => (
                    <tr key={f.id}>
                      <td>{f.filename}</td>
                      <td><Link href={`/programs/${f.program_id}`} className="ov-link">{f.program_name}</Link></td>
                      <td>{f.rows ?? "Not set"}</td>
                      <td>{size(f.size_bytes)}</td>
                      <td>{f.uploaded_by ?? "Unknown"}</td>
                      <td>{formatRelativeTime(f.uploaded_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <Card padding="lg" style={{ maxWidth: 560 }}>
        <CardHeader><CardTitle>Export data</CardTitle></CardHeader>
        {role !== "owner" ? (
          <p className="settings-note">Only an owner can export organization data.</p>
        ) : (
          <form onSubmit={runExport} className="settings-form">
            <p className="settings-lead">Download everything your organization stored in OptiSynth as a JSON file. Uploaded files are not included. Enter your password to continue.</p>
            <label className="settings-label" htmlFor="export-pw">Password</label>
            <input id="export-pw" type="password" autoComplete="current-password" className="settings-field" value={password} onChange={(e) => setPassword(e.target.value)} required />
            {exportError && <ErrorCallout message={exportError} />}
            <div className="settings-actions"><Button type="submit" variant="primary" size="sm" loading={busy}>Export data</Button></div>
            {exported && <p className="settings-ok" role="status">Export downloaded</p>}
          </form>
        )}
      </Card>
    </div>
  );
}
