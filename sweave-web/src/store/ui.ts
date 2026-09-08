import { create } from "zustand";

interface UIState {
  createProjectOpen: boolean;
  setCreateProjectOpen: (open: boolean) => void;
  commandOpen: boolean;
  setCommandOpen: (open: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
  createProjectOpen: false,
  setCreateProjectOpen: (open) => set({ createProjectOpen: open }),
  commandOpen: false,
  setCommandOpen: (open) => set({ commandOpen: open }),
}));
