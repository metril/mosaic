<?php
// PHP section

// set some variables
// Image directory! fill in! relative to root
$imageDir = '/images/';
define('SERVERPATH', '/www'.$imageDir);
define('HTTPPATH', 'https://screensaver.azureolympos.com'.$imageDir);

// Check if this is an API request
if (isset($_GET['api']) && $_GET['api'] === 'random-image') {
    // API endpoint - return a random image
    header("Cache-Control: no-cache, no-store, must-revalidate");
    header("Pragma: no-cache");
    header("Expires: 0");
    
    // Get all image files
    $images = array();
    $dir = opendir(SERVERPATH);
    while (false !== ($file = readdir($dir))) {
        if (preg_match('/(\.jpg|\.png|\.bmp)$/i', $file)) {
            $images[] = $file;
        }
    }
    closedir($dir);
    
    if (empty($images)) {
        // No images found
        header("HTTP/1.1 404 Not Found");
        header("Content-Type: application/json");
        echo json_encode(array("error" => "No images found"));
        exit;
    }
    
    // Select a random image
    $randomImage = $images[array_rand($images)];
    $imagePath = SERVERPATH . $randomImage;
    
    // Check if file exists
    if (!file_exists($imagePath)) {
        header("HTTP/1.1 404 Not Found");
        header("Content-Type: application/json");
        echo json_encode(array("error" => "Image file not found"));
        exit;
    }
    
    // Determine content type based on file extension
    $extension = strtolower(pathinfo($randomImage, PATHINFO_EXTENSION));
    switch ($extension) {
        case 'jpg':
        case 'jpeg':
            $contentType = 'image/jpeg';
            break;
        case 'png':
            $contentType = 'image/png';
            break;
        case 'bmp':
            $contentType = 'image/bmp';
            break;
        default:
            $contentType = 'application/octet-stream';
    }
    
    // Set headers for image delivery
    header("Content-Type: $contentType");
    header("Content-Length: " . filesize($imagePath));
    header("Content-Disposition: inline; filename=\"$randomImage\"");
    
    // Output the image
    readfile($imagePath);
    exit;
}

// Original functionality continues below for the web interface
header("cache-control: no-cache, no-store");

// read the names of images from the image directory
$dir = opendir(SERVERPATH);
$javascriptArray = null;
$i = null;
while (false !== ($file = readdir($dir))) {
        if (preg_match('/(\.jpg|\.png|\.bmp)$/i', $file)){
            $javascriptArray .= $i.'"'.HTTPPATH.$file.'"';
            $i = ',';
        }
}
closedir($dir);

// Html section
?><!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
"http://www.w3.org/TR/html4/strict.dtd">

<html lang="en">
        <head>
                <meta http-equiv="content-type" content="text/html; charset=iso-8859-1">
                <meta http-equiv="refresh" content="300">
                <title>Rotate Images</title>
                <script type="text/javascript">
                        rotatingImages = new Array(<?php echo $javascriptArray; ?>);
                        imageCount = rotatingImages.length;
                        firstTime = true;
                        duration = "30"; //seconds
                
                        function rotateImage(){
                                // Cycle through images sequencially starting with a random image
                                // Do not update the image if loading is not yet completed
                                if (document.getElementById('rotatingImage').complete || firstTime){
                                        if (firstTime) {
                                                thisImage = Math.floor((Math.random() * imageCount))
                                                firstTime = false
                                        }else{
                                                thisImage++
                                                if (thisImage == imageCount) {
                                                        thisImage = 0
                                                }
                                        }
                                        document.getElementById('rotatingImage').src = rotatingImages[thisImage]
					setTimeout("rotateImage()", duration * 1000)
                                }
                        }
                </script>
                <style type="text/css">
                        #rotatingImage{
                                border: none;
                                max-height: 100vh;
                                max-width: 100vw;
                                height: 100%;
                                width: 100%;
                        }
                        body{
                                margin-top: 0px;
                                margin-bottom: 0px;
                                margin-right: 0px;
                                margin-left: 0px;
                                background-color: rgb(0,0,0);
                        }
                </style>
        </head>
        <body>
                        <img id="rotatingImage" src="" alt="">
                <script type="text/javascript">
                        rotateImage();
                </script>
        </body>
</html>