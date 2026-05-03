import { useEffect, useState } from 'react';
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

export default function InventoryList() {
  const [inventory, setInventory] = useState([]);
  const [error, setError] = useState(null);

  const fetchInventory = async () => {
    const token = localStorage.getItem('access_token');
    try {
      const response = await axios.get(`${API_BASE_URL}/api/inventory/`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setInventory(response.data);
    } catch (err) {
      setError('Failed to load inventory table.');
    }
  };

  // Fetch the data when the component first loads
  useEffect(() => {
    fetchInventory();
  }, []);

  const handleDelete = async (inventoryId) => {
    // Confirm before deleting
    if (!window.confirm("Are you sure you want to remove this inventory row?")) return;

    const token = localStorage.getItem('access_token');
    try {
      await axios.delete(`${API_BASE_URL}/api/inventory/${inventoryId}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      alert('Inventory row removed successfully!');
      fetchInventory(); // Refresh the table
    } catch (err) {
      // This will fire if a 'viewer' tries to click delete!
      alert(err.response?.data?.detail || 'Delete failed.');
    }
  };

  return (
    <div style={{ marginTop: '40px' }}>
      <h2>Current Inventory</h2>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      
      <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', marginTop: '10px' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #ccc', paddingBottom: '10px' }}>
            <th>Product Name</th>
            <th>SKU</th>
            <th>Stock</th>
            <th>Selling Price</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {inventory.map((item) => (
            <tr key={item.inventory_id} style={{ borderBottom: '1px solid #eee', height: '40px' }}>
              <td>{item.products?.product_name || 'Unknown'}</td>
              <td>{item.products?.sku_code || 'N/A'}</td>
              <td>{item.stock_quantity}</td>
              <td>${item.selling_price}</td>
              <td>
                <button 
                  onClick={() => handleDelete(item.inventory_id)} 
                  style={{ color: 'red', border: '1px solid red', background: 'none', cursor: 'pointer', padding: '5px 10px', borderRadius: '4px' }}
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
          {inventory.length === 0 && (
            <tr><td colSpan="5" style={{ textAlign: 'center', paddingTop: '20px' }}>No inventory found.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}