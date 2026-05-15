import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Filter, Pencil, RefreshCw, Search } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import {
  exportProjectCsv,
  listProjects,
  listSignatures,
  reviewSignature,
  type Project,
  type ReviewPayload,
  type SignatureRow,
} from "../api/client";

const PAGE_SIZE = 50;
const FILTERS = [
  { id: "all", label: "All" },
  { id: "approved", label: "Approved" },
  { id: "review", label: "Needs Review" },
  { id: "rejected", label: "Rejected" },
  { id: "duplicate", label: "Duplicate" },
] as const;

function statusClass(status: string): string {
  if (status === "approved") return "bg-emerald-50 text-emerald-800";
  if (status === "review") return "bg-amber-50 text-amber-800";
  if (status === "rejected") return "bg-rose-50 text-rose-800";
  if (status === "duplicate") return "bg-violet-50 text-violet-800";
  return "bg-slate-100 text-slate-700";
}

function confidenceColor(value: number): string {
  if (value >= 85) return "bg-emerald-600";
  if (value >= 70) return "bg-amber-600";
  return "bg-rose-600";
}

function projectLabel(project: Project): string {
  const filename = project.pdf_path.split("/").pop() || project.pdf_path;
  return `${project.county ? `[${project.county}] ` : ""}${project.id} - ${filename}`;
}

export function ReviewQueue() {
  const navigate = useNavigate();
  const params = useParams();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<SignatureRow | null>(null);
  const selectedProjectId = params.projectId ?? "";

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  const signaturesQuery = useQuery({
    queryKey: ["signatures", selectedProjectId, filter, page],
    queryFn: () =>
      listSignatures({
        projectId: selectedProjectId,
        status: filter,
        page,
        pageSize: PAGE_SIZE,
      }),
    enabled: Boolean(selectedProjectId),
  });

  const reviewMutation = useMutation({
    mutationFn: reviewSignature,
    onSuccess: async () => {
      setEditing(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["signatures", selectedProjectId] }),
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
      ]);
    },
  });

  const totalPages = Math.max(1, Math.ceil((signaturesQuery.data?.total ?? 0) / PAGE_SIZE));
  const rows = signaturesQuery.data?.items ?? [];

  const visibleProjects = useMemo(
    () => [...projects].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")),
    [projects],
  );

  function selectProject(projectId: string) {
    setPage(1);
    if (projectId) navigate(`/projects/${projectId}`);
    else navigate("/");
  }

  function changeFilter(next: string) {
    setFilter(next);
    setPage(1);
  }

  return (
    <div className="min-h-screen bg-page text-zinc-900">
      <header className="bg-ink px-6 py-4 text-white">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-bold tracking-[0]">Petition Signature Verifier</h1>
            <p className="mt-0.5 text-xs text-indigo-200">
              Voter roll matching · Duplicate detection · Staff review
            </p>
          </div>
          <button
            className="inline-flex h-9 items-center gap-2 rounded-md border border-white/20 px-3 text-sm text-white hover:bg-white/10"
            onClick={() => {
              void projectsQuery.refetch();
              void signaturesQuery.refetch();
            }}
            type="button"
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </header>

      {selectedProject ? <SummaryBanner project={selectedProject} /> : null}

      <section className="border-b border-zinc-200 bg-white px-6 py-3">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3">
          <label className="text-sm font-semibold text-zinc-600" htmlFor="project">
            Project
          </label>
          <select
            id="project"
            value={selectedProjectId}
            onChange={(event) => selectProject(event.target.value)}
            className="h-9 min-w-72 rounded-md border border-zinc-300 bg-white px-3 text-sm"
          >
            <option value="">Select project</option>
            {visibleProjects.map((project) => (
              <option key={project.id} value={project.id}>
                {projectLabel(project)} ({project.approved} valid / {project.total_lines} total)
              </option>
            ))}
          </select>

          <div className="ml-0 flex flex-wrap items-center gap-2 md:ml-4">
            <span className="inline-flex items-center gap-1 text-sm font-semibold text-zinc-600">
              <Filter size={15} />
              Status
            </span>
            {FILTERS.map((item) => (
              <button
                key={item.id}
                className={[
                  "h-8 rounded-full border px-3 text-sm font-medium",
                  filter === item.id
                    ? "border-ink bg-ink text-white"
                    : "border-zinc-300 bg-white text-zinc-700 hover:border-zinc-500",
                ].join(" ")}
                onClick={() => changeFilter(item.id)}
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>

          <button
            className="ml-auto inline-flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!selectedProjectId}
            onClick={() => exportProjectCsv(selectedProjectId)}
            type="button"
          >
            <Download size={16} />
            Export CSV
          </button>
        </div>
      </section>

      <main className="mx-auto max-w-[1400px] px-6 py-5">
        <div className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-black/5">
          <table className="w-full border-collapse">
            <thead className="bg-zinc-100">
              <tr>
                {["#", "Pg", "Extracted Name", "Extracted Address", "Date", "Sig", "Best Voter Match", "Name / Addr", "Confidence", "Status", "Action"].map((head) => (
                  <th
                    key={head}
                    className="px-3 py-3 text-left text-xs font-semibold uppercase tracking-[0] text-zinc-600"
                  >
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!selectedProjectId ? (
                <EmptyRow message="Select a project above." />
              ) : signaturesQuery.isLoading ? (
                <EmptyRow message="Loading signatures..." />
              ) : signaturesQuery.isError ? (
                <EmptyRow message={(signaturesQuery.error as Error).message} />
              ) : rows.length === 0 ? (
                <EmptyRow message="No signatures match this filter." />
              ) : (
                rows.map((signature) => (
                  <SignatureTableRow
                    key={`${signature.line_number}-${signature.status}`}
                    signature={signature}
                    onEdit={setEditing}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>

        {selectedProjectId && totalPages > 1 ? (
          <div className="mt-4 flex justify-center gap-2">
            <button
              className="h-9 rounded border border-zinc-300 bg-white px-3 text-sm disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((value) => value - 1)}
              type="button"
            >
              Prev
            </button>
            <span className="grid h-9 min-w-24 place-items-center text-sm text-zinc-600">
              Page {page} of {totalPages}
            </span>
            <button
              className="h-9 rounded border border-zinc-300 bg-white px-3 text-sm disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
              type="button"
            >
              Next
            </button>
          </div>
        ) : null}
      </main>

      {editing ? (
        <ReviewModal
          signature={editing}
          saving={reviewMutation.isPending}
          error={reviewMutation.error instanceof Error ? reviewMutation.error.message : ""}
          onClose={() => setEditing(null)}
          onSave={(payload) => {
            if (!selectedProjectId) return;
            reviewMutation.mutate({
              projectId: selectedProjectId,
              lineNumber: editing.line_number,
              payload,
            });
          }}
        />
      ) : null}
    </div>
  );
}

function SummaryBanner({ project }: { project: Project }) {
  return (
    <section className="bg-gradient-to-r from-emerald-900 to-emerald-700 px-6 py-4 text-white">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-6">
        <div>
          <div className="text-5xl font-extrabold leading-none">{project.approved}</div>
          <div className="mt-1 text-sm font-semibold text-emerald-100">Valid Signatures</div>
        </div>
        <Metric label="Needs Review" value={project.review} tone="text-amber-200" />
        <Metric label="Rejected" value={project.rejected} tone="text-rose-200" />
        <Metric label="Duplicates" value={project.duplicates} tone="text-violet-200" />
        <Metric label="Total Lines" value={project.total_lines} tone="text-white" />
      </div>
    </section>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold ${tone}`}>{value}</div>
      <div className="mt-1 text-xs font-semibold uppercase tracking-[0] text-white/75">{label}</div>
    </div>
  );
}

function SignatureTableRow({
  signature,
  onEdit,
}: {
  signature: SignatureRow;
  onEdit: (signature: SignatureRow) => void;
}) {
  const confidence = signature.match_confidence == null ? null : Math.round(signature.match_confidence);
  return (
    <tr className="border-t border-zinc-100 hover:bg-zinc-50">
      <td className="px-3 py-3 text-sm">{signature.line_number}</td>
      <td className="px-3 py-3 text-sm">{signature.page}</td>
      <td className="max-w-48 px-3 py-3 text-sm font-medium">{signature.raw_name || "—"}</td>
      <td className="max-w-72 px-3 py-3 text-sm">{signature.raw_address || "—"}</td>
      <td className="px-3 py-3 text-sm">{signature.raw_date || "—"}</td>
      <td className="px-3 py-3 text-sm">{signature.signature_present ? "Yes" : "—"}</td>
      <td className="max-w-64 px-3 py-3 text-sm">
        {signature.voter_name ? (
          <>
            <div className="font-semibold">{signature.voter_name}</div>
            <div className="text-xs text-zinc-500">{signature.voter_address || ""}</div>
            <div className="text-xs text-zinc-400">ID: {signature.voter_id || ""}</div>
          </>
        ) : (
          <span className="text-zinc-400">No match</span>
        )}
      </td>
      <td className="px-3 py-3 text-sm">
        {signature.name_score == null ? (
          <span className="text-zinc-400">—</span>
        ) : (
          <span className="text-xs text-zinc-700">
            Name <b>{Math.round(signature.name_score)}</b>
            <br />
            Addr <b>{Math.round(signature.address_score ?? 0)}</b>
          </span>
        )}
      </td>
      <td className="px-3 py-3">
        {confidence == null ? (
          <span className="text-sm text-zinc-400">—</span>
        ) : (
          <div className="flex min-w-28 items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded bg-zinc-200">
              <div className={`h-full rounded ${confidenceColor(confidence)}`} style={{ width: `${confidence}%` }} />
            </div>
            <span className="w-8 text-right text-xs text-zinc-600">{confidence}</span>
          </div>
        )}
      </td>
      <td className="px-3 py-3">
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold uppercase ${statusClass(signature.status)}`}>
          {signature.status}
        </span>
      </td>
      <td className="px-3 py-3">
        <button
          className="inline-flex h-8 items-center gap-1 rounded border border-zinc-300 bg-white px-2.5 text-sm hover:bg-zinc-100"
          onClick={() => onEdit(signature)}
          type="button"
        >
          <Pencil size={14} />
          Edit
        </button>
      </td>
    </tr>
  );
}

function EmptyRow({ message }: { message: string }) {
  return (
    <tr>
      <td className="px-4 py-16 text-center text-sm text-zinc-400" colSpan={11}>
        <div className="inline-flex items-center gap-2">
          <Search size={16} />
          {message}
        </div>
      </td>
    </tr>
  );
}

function ReviewModal({
  signature,
  saving,
  error,
  onClose,
  onSave,
}: {
  signature: SignatureRow;
  saving: boolean;
  error: string;
  onClose: () => void;
  onSave: (payload: ReviewPayload) => void;
}) {
  const [override, setOverride] = useState(signature.status);
  const [voterId, setVoterId] = useState(signature.voter_id ?? "");
  const [notes, setNotes] = useState(signature.staff_notes ?? "");

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/45 px-4">
      <div className="w-full max-w-xl rounded-lg bg-white p-6 shadow-xl">
        <h2 className="text-lg font-bold">Review Signature #{signature.line_number}</h2>
        <div className="mt-3 rounded-md bg-zinc-50 p-3 text-sm leading-6 text-zinc-600">
          <div>
            Extracted: <b>{signature.raw_name || "—"}</b> | {signature.raw_address || "—"}
          </div>
          <div>
            Best match:{" "}
            {signature.voter_name ? (
              <>
                <b>{signature.voter_name}</b> · {signature.voter_address || ""} · ID{" "}
                {signature.voter_id || ""}
              </>
            ) : (
              "none"
            )}
          </div>
        </div>

        <label className="mt-4 block text-sm font-semibold text-zinc-600" htmlFor="override">
          Override status
        </label>
        <select
          id="override"
          className="mt-1 h-10 w-full rounded-md border border-zinc-300 px-3 text-sm"
          value={override}
          onChange={(event) => setOverride(event.target.value)}
        >
          <option value="approved">Approved</option>
          <option value="review">Needs review</option>
          <option value="rejected">Rejected</option>
          <option value="duplicate">Duplicate</option>
        </select>

        <label className="mt-4 block text-sm font-semibold text-zinc-600" htmlFor="voterId">
          Correct voter ID
        </label>
        <input
          id="voterId"
          className="mt-1 h-10 w-full rounded-md border border-zinc-300 px-3 text-sm"
          value={voterId}
          onChange={(event) => setVoterId(event.target.value)}
          placeholder="Optional"
        />

        <label className="mt-4 block text-sm font-semibold text-zinc-600" htmlFor="notes">
          Notes
        </label>
        <textarea
          id="notes"
          className="mt-1 min-h-20 w-full rounded-md border border-zinc-300 px-3 py-2 text-sm"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />

        {error ? <div className="mt-3 text-sm text-rose-700">{error}</div> : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            className="h-10 rounded-md border border-zinc-300 px-4 text-sm font-semibold"
            onClick={onClose}
            type="button"
          >
            Cancel
          </button>
          <button
            className="h-10 rounded-md bg-ink px-4 text-sm font-semibold text-white disabled:opacity-50"
            disabled={saving}
            onClick={() =>
              onSave({
                override,
                voter_id: voterId || null,
                notes,
              })
            }
            type="button"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
