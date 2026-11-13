import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface User {
  id: number;
  name: string;
  role: string;
}

interface AppState {
  user: User | null;
  darkMode: boolean;
  setUser: (user: User | null) => void;
  toggleDarkMode: () => void;
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      user: null,
      darkMode: false,
      setUser: (user) => set({ user }),
      toggleDarkMode: () => set((state) => {
        const newMode = !state.darkMode;
        if (newMode) document.documentElement.classList.add('dark');
        else document.documentElement.classList.remove('dark');
        return { darkMode: newMode };
      }),
    }),
    {
      name: 'uniespinal-storage', // Nombre en el localStorage
      onRehydrateStorage: () => (state) => {
        // Al cargar la página, aplica el modo oscuro si estaba guardado
        if (state?.darkMode) {
          document.documentElement.classList.add('dark');
        }
      }
    }
  )
);