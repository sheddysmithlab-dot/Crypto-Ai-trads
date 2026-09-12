<?php
/**
 * Same-origin proxy: aitrads.in/__api/* → https://api.aitrads.in/*
 * Login/REST from India hit Hostinger (reachable), Hostinger calls the VPS.
 */
header('Cache-Control: no-store');

$origin = 'https://api.aitrads.in';
$path = isset($_GET['__path']) ? (string) $_GET['__path'] : '';
$path = ltrim($path, '/');
if (strpos($path, '..') !== false) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo '{"message":"Bad path"}';
    exit;
}

$query = $_GET;
unset($query['__path']);
$qs = http_build_query($query);
$url = $origin . '/' . $path;
if ($qs !== '') {
    $url .= '?' . $qs;
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$body = file_get_contents('php://input');

$headers = [];
if (!empty($_SERVER['CONTENT_TYPE'])) {
    $headers[] = 'Content-Type: ' . $_SERVER['CONTENT_TYPE'];
}
$auth = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
if ($auth !== '') {
    $headers[] = 'Authorization: ' . $auth;
}

$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HEADER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_TIMEOUT => 45,
    CURLOPT_HTTPHEADER => $headers,
]);
if ($method !== 'GET' && $method !== 'HEAD') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
}

$raw = curl_exec($ch);
if ($raw === false) {
    http_response_code(502);
    header('Content-Type: application/json');
    echo '{"message":"Cannot reach trading server."}';
    exit;
}

$status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
$headerSize = (int) curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);

$respHeaders = substr($raw, 0, $headerSize);
$respBody = substr($raw, $headerSize);
http_response_code($status > 0 ? $status : 502);

foreach (explode("\r\n", $respHeaders) as $line) {
    if ($line === '' || stripos($line, 'HTTP/') === 0) {
        continue;
    }
    $name = strtolower(strtok($line, ':'));
    if (in_array($name, ['transfer-encoding', 'content-length', 'connection'], true)) {
        continue;
    }
    header($line, false);
}

echo $respBody;
