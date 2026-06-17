-- T347: add the filename column to the documents table (display filename)
-- file_path stores the full path, and filename stores only os.path.basename(file_path)
ALTER TABLE documents ADD COLUMN filename TEXT;
