<?php
// api.php - 简化版：仅隐藏路径，不校验登录
header('Content-Type: application/json');
header('Cache-Control: no-cache, must-revalidate');

$json_file = 'data/merged_events_latest.json';

if (file_exists($json_file)) {
    echo file_get_contents($json_file);
} else {
    header('HTTP/1.1 404 Not Found');
    echo json_encode(['error' => 'Data file missing']);
}