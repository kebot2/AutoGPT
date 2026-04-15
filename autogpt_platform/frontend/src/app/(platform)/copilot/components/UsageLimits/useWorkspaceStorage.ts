import { useQuery } from "@tanstack/react-query";
import { customMutator } from "@/app/api/mutators/custom-mutator";

type StorageUsage = {
  used_bytes: number;
  limit_bytes: number;
  used_percent: number;
  file_count: number;
};

export function useWorkspaceStorage() {
  return useQuery({
    queryKey: ["workspace", "storage", "usage"],
    queryFn: async () => {
      const res = await customMutator<{
        data: StorageUsage;
        status: number;
        headers: Headers;
      }>("/api/workspace/storage/usage", { method: "GET" });
      return res.data;
    },
    staleTime: 30000,
    refetchInterval: 60000,
  });
}
