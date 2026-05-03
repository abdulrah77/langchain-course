import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Login from './Login';
import Dashboard from './Dashboard';
import PublicInventory from './PublicInventory';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Login />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/public/inventory/:token" element={<PublicInventory />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;