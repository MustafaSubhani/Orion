export type Session = {
  token: string;
  username: string;
  role: string;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
};

export type ChatEvent = {
  type: string;
  message?: string;
  response?: string;
  agents_used?: string[];
  request_id?: string;
  success?: boolean;
};
