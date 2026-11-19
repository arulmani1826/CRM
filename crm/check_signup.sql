SELECT name FROM `tabDocType` WHERE name LIKE '%Website%';
SELECT name FROM `tabSingles` WHERE doctype='Website Settings' AND field IN ('disable_signup','disable_user_pass_login');
