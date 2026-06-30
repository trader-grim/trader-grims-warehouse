import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ebay_draft_audit import run_audit


class TestEbayDraftAudit(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.inbox_dir = self.test_dir / 'docs/TGW-Plan-Vault/inbox'
        self.inbox_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch('tools.ebay_draft_audit.load_config')
    @patch('tools.ebay_draft_audit.find_item_jsons')
    @patch('tools.ebay_draft_audit.load_item_doc')
    @patch('tools.ebay_draft_audit.Path')
    def test_run_audit(self, mock_path, mock_load_item, mock_find_items, mock_load_config):
        # Mock configuration
        mock_load_config.return_value = {'itemdata_root': self.test_dir}
        
        # Mock finding items
        json_path = self.test_dir / 'sku1.json'
        mock_find_items.return_value = [json_path]
        
        # Mock loading an item with draft_listing
        mock_load_item.return_value = {
            'draft_listing': {
                'category_id': '123',
                'category_name': 'Test Category',
                'aspects_required_total': 10,
                'aspects_required_filled': 8,
                'aspects_recommended_total': 5,
                'aspects_recommended_filled': 2,
            }
        }
        
        # Mock Path to redirect file writes to our test directory
        def side_effect_path(p):
            if 'docs/TGW-Plan-Vault/inbox/ebay_draft_audit.json' in str(p):
                return self.test_dir / 'ebay_draft_audit.json'
            return Path(p)
        mock_path.side_effect = side_effect_path
        
        # Run audit
        run_audit()
        
        # Check report
        report_path = self.test_dir / 'ebay_draft_audit.json'
        self.assertTrue(report_path.exists())
        
        with open(report_path, 'r') as f:
            report = json.load(f)
            
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]['category'], '123 (Test Category)')
        self.assertEqual(report[0]['req_fill_rate'], 0.8)
        self.assertEqual(report[0]['rec_fill_rate'], 0.4)
        # Check recommendations
        self.assertIn('recommendation', report[0])

if __name__ == '__main__':
    unittest.main()
