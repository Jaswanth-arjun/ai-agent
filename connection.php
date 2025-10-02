
<?php 
$host = 'profound-jade-orca-vpmg5-mysql.profound-jade-orca-vpmg5.svc.cluster.local';
$username = 'mink';
$password = 'wK2+fH1_wU4=pO4-zJ0_';
$database = 'profound-jade-orca'; // Use the original database name

$con = mysqli_connect($host, $username, $password, $database);

// Check connection
if (!$con) {
    die("Connection failed: " . mysqli_connect_error());
}
?>
