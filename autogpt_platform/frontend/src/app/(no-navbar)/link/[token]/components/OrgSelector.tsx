import { Select } from "@/components/atoms/Select/Select";
import { useOrgTeamStore } from "@/services/org-team/store";

interface Props {
  selectedOrgId: string | null;
  onOrgChange: (orgId: string) => void;
}

export function OrgSelector({ selectedOrgId, onOrgChange }: Props) {
  const { orgs, isLoaded } = useOrgTeamStore();

  if (!isLoaded || orgs.length <= 1) return null;

  const options = orgs.map((org) => ({
    value: org.id,
    label: org.isPersonal ? `${org.name} (Personal)` : org.name,
  }));

  return (
    <div className="w-full">
      <Select
        id="org-select"
        label="Billing organization"
        placeholder="Select organization"
        options={options}
        value={selectedOrgId ?? undefined}
        onValueChange={onOrgChange}
      />
    </div>
  );
}
