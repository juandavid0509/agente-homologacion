import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';

export default function Register() {
  const [formData, setFormData] = useState({
    documento: '', 
    codigo: '', 
    nombres: '', 
    apellidos: '', 
    correo: '',
    password: '', 
    programa_id: 1
  });
  
  const [loadingProgramas, setLoadingProgramas] = useState(true);

  const [programas, setProgramas] = useState<{id: number, nombre: string}[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const navigate = useNavigate();

  // Cargar los programas apenas se abre la página


   useEffect(() => {
    const fetchProgramas = async () => {
      try {
        setLoadingProgramas(true);
        const res = await api.get('/programas');
        console.log("Programas recibidos:", res.data); // Para debug en consola
        setProgramas(res.data);
        if (res.data.length > 0) {
          setFormData(prev => ({ ...prev, programa_id: res.data[0].id }));
        }
      } catch (error) {
        console.error("Error cargando programas. Verifica que el backend esté encendido.", error);
      } finally {
        setLoadingProgramas(false);
      }
    };
    fetchProgramas();
  }, []);


  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    try {
      await api.post('/register', formData);
      alert("Cuenta creada exitosamente.");
      navigate('/');
    } catch (error: any) {
      console.error("Error completo:", error.response?.data);
      // FastAPI a veces devuelve el error como un objeto o array
      let msgError = "Error al registrar";
      if (Array.isArray(error.response?.data?.detail)) {
        msgError = error.response.data.detail[0].msg;
      } else if (error.response?.data?.detail) {
        msgError = error.response.data.detail;
      }
      alert(msgError);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100 dark:bg-gray-900 p-4">
      <div className="bg-white dark:bg-gray-800 p-8 rounded-2xl shadow-xl w-full max-w-md">
        <h2 className="text-2xl font-bold mb-6 text-center">Crear Cuenta UniEspinal</h2>
        <form onSubmit={handleRegister} className="space-y-4">
          <input type="text" placeholder="Documento de Identidad" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.documento} onChange={e => setFormData({...formData, documento: e.target.value})} required />
          <input type="text" placeholder="Código Estudiantil" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.codigo} onChange={e => setFormData({...formData, codigo: e.target.value})} required />
          <input type="text" placeholder="Nombres" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.nombres} onChange={e => setFormData({...formData, nombres: e.target.value})} required />
          <input type="text" placeholder="Apellidos" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.apellidos} onChange={e => setFormData({...formData, apellidos: e.target.value})} required />
           <input type="email" placeholder="Correo Electrónico" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.correo} onChange={e => setFormData({...formData, correo: e.target.value})} required />
          <input type="password" placeholder="Contraseña" className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" value={formData.password} onChange={e => setFormData({...formData, password: e.target.value})} required />
          
          <div>
            <label className="block text-sm text-gray-500 mb-1">Programa Académico</label>
            <select 
              className="w-full p-3 rounded-lg border dark:bg-gray-700 dark:border-gray-600" 
              value={formData.programa_id} 
              onChange={e => setFormData({...formData, programa_id: Number(e.target.value)})}
              disabled={loadingProgramas}
            >
              {loadingProgramas ? (
                <option value="0">Cargando programas...</option>
              ) : programas.length === 0 ? (
                <option value="0">No se encontraron programas</option>
              ) : (
                programas.map(prog => (
                  <option key={prog.id} value={prog.id}>{prog.nombre}</option>
                ))
              )}
            </select>
          </div>

          <button type="submit" disabled={isLoading} className="w-full bg-accent text-white p-3 rounded-lg font-semibold hover:bg-blue-700 transition disabled:opacity-50">
            {isLoading ? 'Registrando...' : 'Registrarse'}
          </button>
        </form>
        <div className="mt-4 text-center">
          <button onClick={() => navigate('/')} className="text-accent font-semibold hover:underline text-sm">Volver al Login</button>
        </div>
      </div>
    </div>
  );
}