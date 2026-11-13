import { useState } from 'react';
import { useStore } from '../store/useStore';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';

export default function LoginModal() {
  const [correo, setCorreo] = useState(''); // <-- Cambiado a correo
  const [password, setPassword] = useState('');
  const setUser = useStore((state) => state.setUser);
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      // Enviamos el correo
      const res = await api.post('/login', { correo, password });
      setUser(res.data);
    } catch (error: any) {
      console.error(error);
      const msg = error.response?.data?.detail || "Error al iniciar sesión";
      alert(msg);
    }
  };

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black/50 backdrop-blur-sm z-50">
      <div className="bg-white dark:bg-gray-800 p-8 rounded-2xl shadow-2xl w-full max-w-md">
        <h2 className="text-2xl font-bold mb-6 text-center">Acceso UniEspinal</h2>
        <form onSubmit={handleLogin} className="space-y-4">
          <input 
            type="email" 
            placeholder="Correo Electrónico" 
            className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600"
            value={correo} onChange={e => setCorreo(e.target.value)} required
          />
          <input 
            type="password" 
            placeholder="Contraseña" 
            className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600"
            value={password} onChange={e => setPassword(e.target.value)} required
          />
          <button type="submit" className="w-full bg-accent text-white p-3 rounded-lg font-semibold hover:bg-blue-700 transition">
            Ingresar
          </button>
        </form>
        <div className="mt-6 text-center">
          <p className="text-sm text-gray-500">¿No tienes cuenta?</p>
          <button onClick={() => navigate('/register')} className="text-accent font-semibold hover:underline">
            Crear una cuenta
          </button>
        </div>
      </div>
    </div>
  );
}