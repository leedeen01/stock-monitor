import { LinkIbkrForm } from "@/components/AuthForms";
import { FlexQuerySetup } from "@/components/FlexQuerySetup";
import { requirePage } from "@/lib/guard";
import { getLink } from "@/lib/ibkr";
import { encryptionConfigured } from "@/lib/secrets";
import { Notice } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function LinkIbkrPage(props: PageProps<"/link-ibkr">) {
  const user = await requirePage("/link-ibkr");
  const search = await props.searchParams;
  const welcome = search.welcome === "1";
  const existing = getLink(user.id);

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-16">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">
          {existing.linked ? "Update your IBKR link" : "Link your IBKR account"}
        </h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          {welcome
            ? "Account created. Linking a brokerage account turns the watchlist into a portfolio — you can skip this and do it later."
            : "Connects your holdings so cost basis and position weighting can sit alongside the valuation history."}
        </p>
      </div>

      {!encryptionConfigured() ? (
        <Notice tone="warn">
          Credential storage is not configured on this server. Set{" "}
          <code>ENCRYPTION_KEY</code> in <code>.env</code> and restart — the
          token is encrypted at rest, so linking is refused without it.
        </Notice>
      ) : (
        <>
          <FlexQuerySetup />

          {existing.linked && (
            <p className="text-xs text-neutral-500 dark:text-neutral-400">
              Currently linked to query{" "}
              <code className="font-mono">{existing.queryId}</code>
              {existing.accountLabel ? ` (${existing.accountLabel})` : ""}.
              Submitting replaces it.
            </p>
          )}

          <LinkIbkrForm />
        </>
      )}
    </main>
  );
}
